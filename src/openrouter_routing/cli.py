from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .api import OpenRouterClient, resolve_model
from .config import AppConfig, config_template, load_config
from .errors import AppError, ConfigError, PlanError
from .mix import (
    TimeWindow,
    accumulate,
    apply_window,
    filter_last_sessions,
    filter_records,
    group_by,
    percent,
    resolve_window,
    validate_project_roots,
)
from .paths import default_config_path, ensure_dir
from .preset import cfg_provider, model_of, new_config
from .pricing import (
    DEFAULT_F,
    DEFAULT_FW,
    DEFAULT_R,
    EndpointScore,
    estimated_cost,
    fmt,
    pct_stat,
    score_endpoint,
)
from .routing import emittable, provider_block, rank
from .sessions import PiSessionSource

_QUIET = False
_VERBOSE = False


def _configure_output(args: Namespace) -> None:
    global _QUIET, _VERBOSE
    _QUIET = bool(getattr(args, "quiet", False))
    _VERBOSE = bool(getattr(args, "verbose", False)) and not _QUIET


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def info(message: str) -> None:
    if _VERBOSE:
        print(message, file=sys.stderr)


def tip(message: str) -> None:
    if not _QUIET:
        print(message, file=sys.stderr)


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="config file (default: XDG config path)")


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress tips on stderr")
    parser.add_argument("-v", "--verbose", action="store_true", help="extra diagnostics on stderr")


def _add_rank_args(parser: argparse.ArgumentParser, *, preset: bool = False) -> None:
    parser.add_argument("--quant", default=None, help="comma separated quantizations (e.g. fp8,fp16)")
    parser.add_argument(
        "--healthy-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="drop endpoints whose status != 0",
    )
    parser.add_argument(
        "--require-cache",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="drop endpoints without cache pricing",
    )
    parser.add_argument(
        "--min-tps", type=float, default=None, metavar="TPS", help="drop endpoints below this throughput"
    )
    parser.add_argument(
        "--tps-pct",
        default=None,
        choices=["p50", "p75", "p90", "p99"],
        help="throughput percentile used by --min-tps (default p90)",
    )
    parser.add_argument(
        "--max-ttft", type=float, default=None, metavar="SECONDS", help="drop endpoints above this TTFT"
    )
    parser.add_argument(
        "--ttft-pct",
        default=None,
        choices=["p50", "p75", "p90", "p99"],
        help="latency percentile used by --max-ttft (default p90)",
    )
    parser.add_argument(
        "--prefer",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="also emit provider.preferred_min_throughput/preferred_max_latency",
    )
    if preset:
        parser.add_argument(
            "--top", type=int, default=None, help="keep only the N cheapest endpoints in order"
        )


def _rank_defaults(args: Namespace, cfg: AppConfig) -> None:
    cfg_rank = cfg.rank
    for name in ("healthy_only", "require_cache", "prefer"):
        if getattr(args, name) is None:
            setattr(args, name, getattr(cfg_rank, name))
    if args.quant is None:
        args.quant = cfg_rank.quant
    if getattr(args, "max_price_from", None) is None:
        args.max_price_from = cfg_rank.max_price_from
    if args.min_tps is None:
        args.min_tps = cfg_rank.min_tps
    if args.max_ttft is None:
        args.max_ttft = cfg_rank.max_ttft
    if args.tps_pct is None:
        args.tps_pct = cfg_rank.tps_pct
    if args.ttft_pct is None:
        args.ttft_pct = cfg_rank.ttft_pct
    if hasattr(args, "top") and args.top is None:
        args.top = cfg_rank.top


def _rank_filters(args: Namespace) -> dict[str, Any]:
    return {
        "healthy_only": bool(args.healthy_only),
        "require_cache": bool(args.require_cache),
        "quant": args.quant,
        "min_tps": args.min_tps,
        "max_ttft": args.max_ttft,
        "tps_pct": args.tps_pct,
        "ttft_pct": args.ttft_pct,
    }


def _load_mix(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    if path == "-":
        return json.load(sys.stdin)
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read mix file {path}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"invalid JSON in mix file {path}: {exc}") from exc


def _window_bounds(rows: list[Any], window: TimeWindow) -> tuple[datetime | None, datetime | None]:
    stamps = [row.timestamp for row in rows if row.timestamp]
    since = window.since or (min(stamps) if stamps else None)
    until = window.until or (max(stamps) if stamps else None)
    return since, until


def _window_label(window: TimeWindow) -> str:
    if window.relative:
        span = f"{window.hours:g}h" if window.hours is not None else f"{window.days:g}d"
        return f"last {span}, anchor {window.anchor}"
    if window.since is not None or window.until is not None:
        return "explicit bounds (inclusive)"
    return "all records"


def _mix_payload(mix: dict[str, Any], endpoint: EndpointScore | None) -> dict[str, Any]:
    payload = {
        key: mix[key]
        for key in (
            "requests",
            "input",
            "output",
            "cacheRead",
            "cacheWrite",
            "total_input",
            "f",
            "R",
            "fw",
        )
    }
    if endpoint is not None:
        payload["estimated_cost_usd"] = round(estimated_cost(mix, endpoint), 6)
    return payload


def _group_payloads(
    rows: list[Any], keys: list[str], endpoint: EndpointScore | None
) -> dict[str, dict[str, dict[str, Any]]]:
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    for key in keys:
        items = sorted(group_by(rows, key).items(), key=lambda item: (-len(item[1]), item[0]))
        groups[key] = {name: _mix_payload(accumulate(group), endpoint) for name, group in items}
    return groups


def _top_projects(
    rows: list[Any], count: int, endpoint: EndpointScore | None
) -> list[tuple[str, dict[str, Any]]]:
    if count < 1:
        raise ConfigError("--top-projects must be >= 1")
    items = [(name, accumulate(group)) for name, group in group_by(rows, "project").items()]
    items.sort(key=lambda item: (-item[1]["total_input"], item[0]))
    return [(name, _mix_payload(mix, endpoint)) for name, mix in items[:count]]


def _cost_endpoint(
    args: Namespace, cfg: AppConfig, overall: dict[str, Any]
) -> tuple[EndpointScore, dict[str, Any]]:
    client = OpenRouterClient()
    resolved, endpoints = resolve_model(args.cost_model, client)
    rows = [score_endpoint(endpoint, overall["f"], overall["R"], overall["fw"]) for endpoint in endpoints]
    rows = [row for row in rows if row.slug]
    result = rank(
        rows,
        healthy_only=cfg.rank.healthy_only,
        require_cache=cfg.rank.require_cache,
        quant=cfg.rank.quant,
        min_tps=cfg.rank.min_tps,
        max_ttft=cfg.rank.max_ttft,
        tps_pct=cfg.rank.tps_pct,
        ttft_pct=cfg.rank.ttft_pct,
    )
    info(f"cost model : {args.cost_model} -> {resolved} (endpoint {result.best.slug})")
    return result.best, {
        "requested": args.cost_model,
        "resolved": resolved,
        "endpoint": result.best.slug,
    }


_TSV_COLUMNS = ("requests", "input", "cacheRead", "cacheWrite", "output", "total_input", "f", "R", "fw")


def _tsv_value(key: str, value: Any) -> str:
    return f"{value:.6f}" if key in {"f", "R", "fw"} else str(value)


def _print_tsv(
    overall: dict[str, Any],
    groups: dict[str, dict[str, dict[str, Any]]],
    top_projects: list[tuple[str, dict[str, Any]]],
) -> None:
    print("\t".join(["scope", "key", "name", *_TSV_COLUMNS, "estimated_cost_usd"]))

    def emit(scope: str, key: str, name: str, payload: dict[str, Any]) -> None:
        cells = [scope, key, name, *(_tsv_value(column, payload[column]) for column in _TSV_COLUMNS)]
        cost = payload.get("estimated_cost_usd")
        cells.append("" if cost is None else f"{cost:.6f}")
        print("\t".join(cells))

    emit("overall", "", "all", overall)
    for key, members in groups.items():
        for name, payload in members.items():
            emit("group", key, name, payload)
    for name, payload in top_projects:
        emit("top-project", "project", name, payload)


def _print_group_table(title: str, members: dict[str, dict[str, Any]], has_cost: bool) -> None:
    header = f"{title:45} {'reqs':>5} {'f':>7} {'R':>8} {'total_input':>13}"
    if has_cost:
        header += f" {'cost':>10}"
    print(header)
    for name, mix in members.items():
        line = (
            f"{name[:45]:45} {mix['requests']:>5} {mix['f']:>7.4f} {mix['R']:>8.5f} {mix['total_input']:>13,}"
        )
        if has_cost:
            line += f" {mix['estimated_cost_usd']:>10.6f}"
        print(line)


def cmd_measure(args: Namespace) -> int:
    cfg = load_config(args.config)
    source = PiSessionSource(pattern=cfg.paths.sessions_glob)
    paths = args.paths or source.discover()
    if not paths:
        raise ConfigError(f"no session files found under {cfg.paths.sessions_glob}")
    info(f"config     : {cfg.source_path} ({'loaded' if cfg.loaded else 'defaults'})")

    records = list(source.iter_records(paths))
    if not records:
        raise ConfigError("no usage records found in the selected session files")
    info(f"sessions   : {len(paths)} file(s), {len(records)} record(s)")

    if args.last_sessions is not None:
        records = filter_last_sessions(records, args.last_sessions)
        info(f"last {args.last_sessions} session(s) -> {len(records)} record(s)")

    hours = args.hours
    days = args.days
    since = args.since
    until = args.until
    if hours is None and days is None and since is None:
        hours = cfg.measure.hours
        days = cfg.measure.days
        since = cfg.measure.since
    if until is None:
        until = cfg.measure.until
    anchor = args.anchor or cfg.measure.anchor

    window = resolve_window(records, hours=hours, days=days, since=since, until=until, anchor=anchor)
    rows = apply_window(records, window)

    models = args.model or list(cfg.measure.models)
    routed_models = args.routed_model or list(cfg.measure.routed_models)
    exclude_models = args.exclude_model or list(cfg.measure.exclude_models)
    providers = args.provider or []
    projects = args.project or list(cfg.measure.projects)
    exclude_projects = args.exclude_project or []
    validate_project_roots([*projects, *exclude_projects])
    rows = filter_records(
        rows,
        models=models,
        routed_models=routed_models,
        exclude_models=exclude_models,
        providers=providers,
        projects=projects,
        exclude_projects=exclude_projects,
    )
    if not rows:
        raise ConfigError("no usage records in the selected window/filters")

    overall = accumulate(rows)
    per_model: dict[str, list[Any]] = {}
    for row in rows:
        per_model.setdefault(f"{row.provider}/{row.model}", []).append(row)

    groups_by = args.by or (["provider-model"] if len(per_model) > 1 else [])
    cost_endpoint = None
    cost_model_payload = None
    if args.cost_model:
        cost_endpoint, cost_model_payload = _cost_endpoint(args, cfg, overall)

    overall_payload = _mix_payload(overall, cost_endpoint)
    groups = _group_payloads(rows, groups_by, cost_endpoint)
    top_projects = (
        _top_projects(rows, args.top_projects, cost_endpoint) if args.top_projects is not None else []
    )

    format_name = "json" if getattr(args, "json", False) else args.format
    since_ts, until_ts = _window_bounds(rows, window)
    if format_name == "json":
        payload = {
            "schema": "openrouter-routing/mix/v1",
            "window": {
                "since": since_ts.isoformat() if since_ts else None,
                "until": until_ts.isoformat() if until_ts else None,
                "anchor": window.anchor,
                "hours": window.hours,
                "days": window.days,
                "last_sessions": args.last_sessions,
                "relative": window.relative,
            },
            "filters": {
                "models": models,
                "routed_models": routed_models,
                "exclude_models": exclude_models,
                "providers": providers,
                "projects": projects,
                "exclude_projects": exclude_projects,
            },
            "groups": groups,
            "top_projects": [{"name": name, **mix} for name, mix in top_projects],
            "cost_model": cost_model_payload,
        }
        payload.update(overall_payload)
        print(json.dumps(payload, indent=2))
        return 0

    if format_name == "tsv":
        _print_tsv(overall_payload, groups, top_projects)
        return 0

    span = "?"
    if since_ts and until_ts:
        span = f"{since_ts:%Y-%m-%d %H:%M:%S} .. {until_ts:%Y-%m-%d %H:%M:%S}"
    print(f"window   : {span}  ({_window_label(window)})")
    print(f"requests : {overall['requests']}")
    print()
    input_share = percent(overall["input"] / overall["total_input"]) if overall["total_input"] else "0.00%"
    print(f"  input      {overall['input']:>12,}  {input_share:>7}  cache misses")
    print(f"  cacheRead  {overall['cacheRead']:>12,}  {percent(overall['f']):>7}  cache hits")
    print(f"  cacheWrite {overall['cacheWrite']:>12,}  {percent(overall['fw']):>7}")
    print(f"  output     {overall['output']:>12,}  {percent(overall['R']):>7}  of input tokens")
    print()
    print(f"  f  = {overall['f']:.4f}      R = {overall['R']:.5f}      fw = {overall['fw']:.5f}")
    if cost_endpoint is not None:
        print(f"  estimated cost on {cost_endpoint.slug}: ${overall_payload['estimated_cost_usd']:.6f}")
    print()
    print("per-1M-input-token cost weights (formula uses these):")
    print(
        f"  {(1 - overall['f']) * 100:6.2f}% x p_in   +  {overall['f'] * 100:6.2f}% x p_cr"
        f"   +  {overall['R'] * 100:6.2f}% x p_out  +  {overall['fw'] * 100:6.2f}% x p_cw"
    )
    miss = 1 - overall["f"]
    if miss > 1e-6:
        print(f"  => {overall['f'] * 100:.1f}% of input tokens are cache hits: a no-cache provider")
        print(f"     must be ~{1 / miss:.1f}x cheaper on p_in to win against an equal-price cached one")
    print()

    if top_projects:
        print(f"top {len(top_projects)} project(s) by input tokens:")
        print(
            f"{'project':45} {'reqs':>5} {'total_input':>13} {'f':>7} {'R':>8}"
            f" {'miss%':>6} {'hit%':>6} {'out%':>6} {'cw%':>5}"
        )
        for name, mix in top_projects:
            print(
                f"{name[:45]:45} {mix['requests']:>5} {mix['total_input']:>13,} "
                f"{mix['f']:>7.4f} {mix['R']:>8.5f} "
                f"{(1 - mix['f']) * 100:>6.2f} {mix['f'] * 100:>6.2f} "
                f"{mix['R'] * 100:>6.2f} {mix['fw'] * 100:>5.2f}"
            )
        print()

    titles = {
        "provider-model": "provider/model",
        "model": "model",
        "routed-model": "routed model",
        "project": "project",
    }
    for key in groups_by:
        _print_group_table(titles[key], groups[key], cost_endpoint is not None)
        print()

    tip(
        "tip: openrouter-routing measure --json > ~/.cache/openrouter-routing/mix.json  &&  "
        "openrouter-routing cheapest <model> --mix ~/.cache/openrouter-routing/mix.json"
    )
    return 0


def cmd_cheapest(args: Namespace) -> int:
    cfg = load_config(args.config)
    _rank_defaults(args, cfg)
    f, r, fw = DEFAULT_F, DEFAULT_R, DEFAULT_FW
    mix = _load_mix(args.mix)
    f = mix.get("f", f)
    r = mix.get("R", r)
    fw = mix.get("fw", fw)
    f = args.f if args.f is not None else f
    r = args.r if args.r is not None else r
    fw = args.fw if args.fw is not None else fw

    client = OpenRouterClient()
    resolved, endpoints = resolve_model(args.model, client)
    if resolved != args.model:
        tip(f"// {args.model} is an alias -> resolving endpoints of {resolved}")
    rows = [score_endpoint(endpoint, f, r, fw) for endpoint in endpoints]
    rows = [row for row in rows if row.slug]
    known_perf = sum(
        1
        for row in rows
        if pct_stat(row.tps, args.tps_pct) is not None or pct_stat(row.ttft, args.ttft_pct) is not None
    )
    if (args.min_tps is not None or args.max_ttft is not None) and not known_perf:
        warn(
            "no p50/p90 stats returned -- pass an API key (OPENROUTER_API_KEY) "
            "or the perf filters cannot be applied"
        )

    result = rank(rows, **_rank_filters(args))
    ranked = result.rows
    best = result.best

    if args.json:
        print(
            json.dumps(
                {
                    "model": args.model,
                    "mix": {"f": f, "R": r, "fw": fw},
                    "cheapest": best.slug,
                    "ranking": [row.as_dict() for row in ranked],
                },
                indent=2,
            )
        )
        return 0

    if args.order or args.provider_json:
        safe, dropped = emittable(ranked)
        if not safe:
            raise PlanError(
                f"{args.model}: every endpoint has status != 0 (model is going away); nothing safe to pin"
            )
        if dropped:
            tip("// dropped from order (status != 0): " + ", ".join(row.slug for row in dropped))

    if args.order:
        print(json.dumps([row.slug for row in emittable(ranked)[0]], indent=2))
        return 0

    if args.provider_json:
        block = provider_block(
            ranked,
            max_price_from=args.max_price_from,
            prefer=bool(args.prefer),
            min_tps=args.min_tps,
            max_ttft=args.max_ttft,
            tps_pct=args.tps_pct,
            ttft_pct=args.ttft_pct,
        )
        safe, _ = emittable(ranked)
        cap = safe[min(args.max_price_from, len(safe) - 1)]
        print(json.dumps(block, indent=2))
        tip(
            f"// order = formula ranking (f={f:.3f} R={r:.4f}); "
            f"max_price from #{min(args.max_price_from, len(safe) - 1)} ({cap.slug})"
        )
        return 0

    print(f"{args.model}   f={f:.4f} R={r:.5f} fw={fw:.5f}   USD per 1M input tokens")
    print(
        f"{'C_eff':>8} {'vs best':>8}  {'slug':26} {'in':>6} {'out':>6} {'cr':>7} "
        f"{'quant':>7} {'tps50':>5} {'tps90':>5} {'ttft90':>6} {'up%':>5} {'st':>3}"
    )
    for row in ranked:
        delta = "—" if row is best else f"+{100 * (row.cost / best.cost - 1):.0f}%"
        cr = row.cr_price if row.cached else float("nan")
        up = row.uptime if isinstance(row.uptime, (int, float)) else float("nan")
        print(
            f"{row.cost:>8.4f} {delta:>8}  {row.slug[:26]:26} {row.in_price:>6.3f} {row.out_price:>6.2f} "
            f"{cr:>7.4f} {str(row.quant)[:7]:>7} "
            f"{fmt(pct_stat(row.tps, 'p50')):>5} {fmt(pct_stat(row.tps, 'p90')):>5} "
            f"{fmt(pct_stat(row.ttft, 'p90')):>6} {up:>5.1f} {str(row.status):>3}"
        )

    if best.cached:
        p_in_rank = sorted(ranked, key=lambda row: row.in_price).index(best) + 1
        cached_rows = [row for row in ranked if row.cached]
        p_cr_rank = sorted(cached_rows, key=lambda row: row.cr_price or 0).index(best) + 1
        print(
            f"\nbest is #{p_in_rank} on input price but #{p_cr_rank} on cache-read price "
            "-> the win comes from caching, which `sort: price` does not model"
        )
    else:
        print("\nbest has no prompt caching: it wins only because the mix assumed no cache hits")
    degraded = [row for row in ranked if row.status != 0]
    if degraded:
        warn(
            f"{len(degraded)} endpoint(s) report status != 0 (not 'ok'); "
            "pin them at your own risk or pass --healthy-only"
        )
    no_cache = [row for row in ranked if not row.cached]
    if no_cache:
        warn(
            f"{len(no_cache)} endpoint(s) cannot cache; they are scored at f=0 "
            f"(e.g. {', '.join(row.slug for row in no_cache[:3])})"
        )
    if args.min_tps is None and args.max_ttft is None:
        tip("--min-tps 40 (p90) / --max-ttft 6 to keep the cheap-but-slow endpoints out")
    tip(f"next: openrouter-routing cheapest {args.model} --mix {args.mix or 'mix.json'} --provider-json")
    return 0


def _preset_client() -> OpenRouterClient:
    return OpenRouterClient()


def cmd_preset_show(args: Namespace) -> int:
    client = _preset_client()
    preset = client.get_preset(args.slug)
    designated = preset.get("designated_version") or {}
    config = designated.get("config") or {}
    provider = cfg_provider(config)
    print(f"preset {preset['slug']}  ({preset['name']}, status={preset['status']})")
    print(
        f"  designated version : {designated.get('version')}  ({str(designated.get('created_at', ''))[:19]})"
    )
    print(f"  updated_at         : {str(preset.get('updated_at', ''))[:19]}")
    print(f"  system_prompt      : {'set' if designated.get('system_prompt') else 'none'}")
    print(f"  model              : {config.get('model') or config.get('models')}")
    print(f"  provider.sort      : {provider.get('sort')}")
    print(f"  provider.order     : {provider.get('order') or []}")
    print(f"  provider.ignore    : {provider.get('ignore') or []}")
    print(f"  provider.quant     : {provider.get('quantizations') or []}")
    print(f"  allow_fallbacks    : {provider.get('allow_fallbacks')}")
    if args.full:
        print("\nfull config:")
        print(json.dumps(config, indent=2))
    if args.versions:
        print(f"\nlast {args.versions} version(s):")
        print(f"  {'ver':>3} {'created':19} {'model':34} {'provider.order'}")
        for version in client.get_versions(args.slug)[: args.versions]:
            cfg = version.get("config") or {}
            print(
                f"  {version['version']:>3} {str(version.get('created_at') or '')[:19]} "
                f"{str(model_of(cfg))[:34]:34} {cfg_provider(cfg).get('order') or []}"
            )
    return 0


def _preset_plan_options(args: Namespace, cfg: AppConfig) -> None:
    _rank_defaults(args, cfg)


def cmd_preset_plan(args: Namespace) -> int:
    cfg = load_config(args.config)
    _preset_plan_options(args, cfg)
    client = _preset_client()
    preset = client.get_preset(args.slug)
    designated = preset.get("designated_version") or {}
    current = designated.get("config") or {}
    mix = _load_mix(args.mix)
    created, rows, resolved = new_config(current, mix, args, client)

    old = cfg_provider(current).get("order") or []
    new = cfg_provider(created)["order"]
    print(f"preset {args.slug} v{designated.get('version')} -> v{(designated.get('version') or 0) + 1}")
    if resolved != (args.model or model_of(current)):
        print(f"  model {model_of(current)} is an alias -> resolving endpoints of {resolved}")
    print(f"  model {created['model']}   f={mix.get('f', 0):.4f} R={mix.get('R', 0):.4f}")
    print(f"\n  {'new':>2}  {'slug':28} {'C_eff':>8} {'was':>9}   in/out/cr")
    for index, row in enumerate(rows, 1):
        was = f"#{old.index(row.slug) + 1}" if row.slug in old else "-"
        gone = "" if row.slug in old else "  NEW"
        tps = pct_stat(row.tps, args.tps_pct)
        print(
            f"  {index:>2}  {row.slug[:28]:28} {row.cost:>8.4f} {was:>9}   "
            f"{row.in_price:.3f}/{row.out_price:.2f}/"
            f"{row.cr_price if row.cached else float('nan'):.4f}  "
            f"tps{args.tps_pct}={'?' if tps is None else f'{tps:.0f}'}{gone}"
        )
    for slug in old:
        if slug not in new:
            print(f"     dropped {slug} (no longer healthy / filtered out)")
    if old == new:
        print("\n  order unchanged")
    print("\n  config to persist:")
    print(json.dumps(created, indent=2))

    if not args.apply:
        print(f"\ndry run. re-run with --apply to POST a v{(designated.get('version') or 0) + 1}")
        return 0
    if old == new and not args.force:
        print("\norder unchanged; not creating a new preset version (use --force to override)")
        return 0
    if designated.get("system_prompt"):
        print(
            "\nwarning: this preset has a system_prompt; the create-from-body endpoint "
            "documents no way to carry it over, check the new version afterwards.",
            file=sys.stderr,
        )
    client.post_preset_config(args.slug, created)
    after = client.get_preset(args.slug).get("designated_version") or {}
    print(f"\nnow at v{after.get('version')}, order={cfg_provider(after.get('config') or {}).get('order')}")
    return 0


def cmd_preset_rollback(args: Namespace) -> int:
    client = _preset_client()
    versions = {version["version"]: version for version in client.get_versions(args.slug)}
    if args.version not in versions:
        raise PlanError(f"version {args.version} not found (have {sorted(versions)})")
    config = versions[args.version].get("config") or {}
    print(f"rollback {args.slug} -> config of v{args.version}:")
    print(json.dumps(config, indent=2))
    if not args.apply:
        print("\ndry run. re-run with --apply (creates a new version with this config)")
        return 0
    client.post_preset_config(args.slug, config)
    after = client.get_preset(args.slug).get("designated_version") or {}
    print(f"now at v{after.get('version')}")
    return 0


def cmd_config_path(args: Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else default_config_path()
    print(path)
    print(f"exists: {path.exists()}")
    return 0


def cmd_config_show(args: Namespace) -> int:
    cfg = load_config(args.config)
    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def cmd_config_init(args: Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else default_config_path()
    if path.exists() and not args.force:
        raise ConfigError(f"{path} already exists; use --force to overwrite")
    ensure_dir(path.parent)
    path.write_text(config_template(), encoding="utf-8")
    print(f"wrote {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openrouter-routing", description="Cache-aware OpenRouter routing for pi"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    measure = sub.add_parser("measure", help="aggregate pi session usage into f/R/fw")
    _add_config_arg(measure)
    _add_output_args(measure)
    measure.add_argument("paths", nargs="*", help="session JSONL files (default: config/pi sessions glob)")
    measure.add_argument("--hours", type=float, default=None, help="only the last N hours")
    measure.add_argument("--days", type=float, default=None, help="only the last N days")
    measure.add_argument(
        "--anchor",
        choices=["latest", "now"],
        default=None,
        help="relative window anchor: latest record (default) or wall clock",
    )
    measure.add_argument(
        "--last-sessions",
        type=int,
        default=None,
        metavar="N",
        help="only the N most recent sessions (applied before the time window)",
    )
    measure.add_argument("--since", help="only entries at/after this ISO timestamp (inclusive)")
    measure.add_argument("--until", help="only entries at/before this ISO timestamp (inclusive)")
    measure.add_argument(
        "--model", action="append", help="filter requested model (substring/glob, repeatable)"
    )
    measure.add_argument("--routed-model", action="append", help="filter upstream responseModel (repeatable)")
    measure.add_argument("--exclude-model", action="append", help="exclude requested model (repeatable)")
    measure.add_argument("--provider", action="append", help="filter pi provider (repeatable)")
    measure.add_argument("--project", action="append", help="session cwd root (repeatable, recursive)")
    measure.add_argument(
        "--exclude-project", action="append", help="exclude a session cwd root (repeatable, recursive)"
    )
    measure.add_argument(
        "--top-projects",
        type=int,
        default=None,
        metavar="N",
        help="print the N projects with the most input tokens",
    )
    measure.add_argument(
        "--cost-model",
        default=None,
        metavar="SLUG",
        help="estimate group cost with this model's cheapest endpoint (network)",
    )
    measure.add_argument(
        "--by", action="append", choices=["project", "model", "routed-model", "provider-model"]
    )
    measure.add_argument(
        "--format",
        choices=["table", "json", "tsv"],
        default="table",
        help="output format (default table; --json is an alias for --format json)",
    )
    measure.add_argument("--json", action="store_true", help="alias for --format json")
    measure.set_defaults(func=cmd_measure)

    cheapest = sub.add_parser("cheapest", help="rank endpoints by the cost of your token mix")
    _add_config_arg(cheapest)
    _add_output_args(cheapest)
    cheapest.add_argument("model", help="model slug, e.g. deepseek/deepseek-chat-v3.1")
    cheapest.add_argument("--mix", help="JSON file/'-' from measure --json")
    cheapest.add_argument("--f", type=float, default=None, help=f"cache-hit share (default {DEFAULT_F})")
    cheapest.add_argument("--r", type=float, default=None, help=f"output/input ratio (default {DEFAULT_R})")
    cheapest.add_argument(
        "--fw", type=float, default=None, help=f"cache-write/input ratio (default {DEFAULT_FW})"
    )
    _add_rank_args(cheapest)
    cheapest.add_argument("--json", action="store_true", help="machine-readable ranking")
    cheapest.add_argument("--order", action="store_true", help="print slug list for provider.order")
    cheapest.add_argument("--provider-json", action="store_true", help="print a provider{} routing block")
    cheapest.add_argument(
        "--max-price-from",
        type=int,
        default=None,
        metavar="N",
        help="set max_price caps from the Nth ranked endpoint (0 = best)",
    )
    cheapest.set_defaults(func=cmd_cheapest)

    preset = sub.add_parser("preset", help="inspect or rewrite an OpenRouter preset")
    preset_sub = preset.add_subparsers(dest="preset_command", required=True)
    show = preset_sub.add_parser("show", help="print a preset and its versions")
    _add_config_arg(show)
    _add_output_args(show)
    show.add_argument("slug")
    show.add_argument("--versions", type=int, default=0, help="also list the last N versions")
    show.add_argument("--full", action="store_true", help="dump the whole config")
    show.set_defaults(func=cmd_preset_show)

    plan = preset_sub.add_parser("plan", help="recompute provider.order with the cost formula")
    _add_config_arg(plan)
    _add_output_args(plan)
    plan.add_argument("slug")
    plan.add_argument("--mix", help="measure --json output (f/R/fw)")
    plan.add_argument("--model", help="override the model the endpoints are fetched for")
    plan.add_argument(
        "--sort", choices=["price", "throughput", "latency", "exacto"], help="provider.sort.by to persist"
    )
    plan.add_argument("--ignore", default=None, help="comma separated provider slugs to ignore")
    plan.add_argument("--allow-fallbacks", dest="allow_fallbacks", action="store_true", default=None)
    plan.add_argument("--no-allow-fallbacks", dest="allow_fallbacks", action="store_false")
    _add_rank_args(plan, preset=True)
    plan.add_argument("--apply", action="store_true", help="POST the new config (creates a version)")
    plan.add_argument("--force", action="store_true", help="apply even when provider.order is unchanged")
    plan.set_defaults(func=cmd_preset_plan)

    rollback = preset_sub.add_parser("rollback", help="re-POST the config of an older version")
    _add_config_arg(rollback)
    _add_output_args(rollback)
    rollback.add_argument("slug")
    rollback.add_argument("--version", type=int, required=True)
    rollback.add_argument("--apply", action="store_true")
    rollback.set_defaults(func=cmd_preset_rollback)

    config = sub.add_parser("config", help="show or initialize the XDG configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_path = config_sub.add_parser("path", help="print the config path")
    _add_config_arg(config_path)
    _add_output_args(config_path)
    config_path.set_defaults(func=cmd_config_path)
    config_show = config_sub.add_parser("show", help="print the effective config")
    _add_config_arg(config_show)
    _add_output_args(config_show)
    config_show.set_defaults(func=cmd_config_show)
    config_init = config_sub.add_parser("init", help="write a starter config")
    _add_config_arg(config_init)
    _add_output_args(config_init)
    config_init.add_argument("--force", action="store_true")
    config_init.set_defaults(func=cmd_config_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_output(args)
    if getattr(args, "json", False):
        args.format = "json"
    try:
        return int(args.func(args) or 0)
    except (AppError, ConfigError, PlanError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def measure_main(argv: list[str] | None = None) -> int:
    return main(["measure", *(sys.argv[1:] if argv is None else argv)])


def cheapest_main(argv: list[str] | None = None) -> int:
    return main(["cheapest", *(sys.argv[1:] if argv is None else argv)])


def preset_main(argv: list[str] | None = None) -> int:
    return main(["preset", *(sys.argv[1:] if argv is None else argv)])
