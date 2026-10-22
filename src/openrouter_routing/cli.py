from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__, automation
from .api import OpenRouterClient, resolve_model
from .config import AppConfig, config_template, load_config
from .doctor import failed as doctor_failed
from .doctor import format_check, run_doctor
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
from .refresh import RefreshResult, run_refresh
from .routing import compare_current, compare_modes, emittable, provider_block, rank
from .sessions import PiSessionSource
from .targets import TargetPlan
from .verify import DEFAULT_LAST, DEFAULT_TOLERANCE, VerifyResult, verify_generations

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


def _comparison_mix(mix: dict[str, Any], f: float, r: float, fw: float) -> dict[str, int]:
    """Use the saved counters, or synthesize 1M input tokens from f/R/fw."""
    counters = {key: int(mix.get(key) or 0) for key in ("input", "cacheRead", "cacheWrite", "output")}
    if any(counters.values()):
        return counters
    return {
        "input": max(0, round((1 - f) * 1_000_000)),
        "cacheRead": max(0, round(f * 1_000_000)),
        "cacheWrite": max(0, round(fw * 1_000_000)),
        "output": max(0, round(r * 1_000_000)),
    }


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


def _print_current_comparison(preset: str, comparison: dict[str, Any]) -> None:
    current = comparison.get("current")
    proposed = comparison.get("proposed") or {}
    order = comparison.get("current_order") or []
    print(f"preset {preset} current order: {_order_text(order)}")
    if current is None:
        print("  current  : (no ordered endpoint still exists in the endpoint list)")
    else:
        print(
            f"  current  : {current['endpoint'][:32]:32} "
            f"C_eff={current['c_eff']:.6f}  mix cost=${current['estimated_cost_usd']:.6f}"
        )
    print(
        f"  proposed : {proposed.get('endpoint', '?')[:32]:32} "
        f"C_eff={proposed.get('c_eff', 0.0):.6f}  mix cost=${proposed.get('estimated_cost_usd', 0.0):.6f}"
    )
    if comparison.get("delta_pct") is not None:
        print(
            f"  change   : ${comparison['delta_usd']:+.6f} "
            f"({comparison['delta_pct']:+.2f}% versus the current order)"
        )
    print("  (mix cost uses the token counts in the mix; use measure --days 7 for a 7-day estimate)")
    print()


def cmd_cheapest(args: Namespace) -> int:
    if args.compare_current and not args.preset:
        raise ConfigError("--compare-current requires --preset SLUG")
    if args.compare_current and (args.order or args.provider_json):
        raise ConfigError("--compare-current cannot be combined with --order or --provider-json")
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
    preset_config: dict[str, Any] | None = None
    if args.preset:
        preset = client.get_preset(args.preset)
        designated = preset.get("designated_version") or {}
        preset_config = designated.get("config") or {}
        if not args.model:
            args.model = model_of(preset_config)
    if not args.model:
        raise ConfigError("cheapest needs a model, or --preset SLUG to use the preset's model")

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
    comparison = None
    if args.compare_current:
        current_order = cfg_provider(preset_config or {}).get("order") or []
        comparison = compare_current(rows, current_order, _comparison_mix(mix, f, r, fw), proposed=best)

    if args.json:
        payload: dict[str, Any] = {
            "model": args.model,
            "mix": {"f": f, "R": r, "fw": fw},
            "cheapest": best.slug,
            "ranking": [row.as_dict() for row in ranked],
        }
        if comparison is not None:
            payload["preset"] = args.preset
            payload["comparison"] = comparison
        print(json.dumps(payload, indent=2))
        return 0

    if comparison is not None:
        _print_current_comparison(args.preset or "?", comparison)

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
    if getattr(args, "routing_mode", None) is None:
        args.routing_mode = "order"


def cmd_preset_plan(args: Namespace) -> int:
    if getattr(args, "dry_run", False):
        args.apply = False
    cfg = load_config(args.config)
    _preset_plan_options(args, cfg)
    client = _preset_client()
    preset = client.get_preset(args.slug)
    designated = preset.get("designated_version") or {}
    current = designated.get("config") or {}
    mix = _load_mix(args.mix)
    created, rows, resolved = new_config(current, mix, args, client)

    old = cfg_provider(current).get("order") or []
    new = cfg_provider(created).get("order") or []
    changed = created != current
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
    if old == new and changed:
        print("\n  order unchanged; other routing/config fields changed")
    elif old == new:
        print("\n  order unchanged")
    print("\n  config to persist:")
    print(json.dumps(created, indent=2))

    if not args.apply:
        print(f"\ndry run. re-run with --apply to POST a v{(designated.get('version') or 0) + 1}")
        return 0
    if not changed and not args.force:
        print("\nconfig unchanged; not creating a new preset version (use --force to override)")
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
    if getattr(args, "dry_run", False):
        args.apply = False
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
    if getattr(args, "dry_run", False):
        print(f"dry run: would write {path}")
        return 0
    ensure_dir(path.parent)
    path.write_text(config_template(), encoding="utf-8")
    print(f"wrote {path}")
    return 0


@contextmanager
def _optional_log(path: Path | None) -> Iterator[Callable[[str], None]]:
    if path is None:
        yield lambda message: None
        return
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:

        def write(message: str) -> None:
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            handle.write(f"{stamp} {message}\n")
            handle.flush()

        yield write


def _order_text(order: Sequence[str]) -> str:
    return ", ".join(order) if order else "(none)"


def _print_refresh_plans(plans: Sequence[TargetPlan], compare: bool) -> None:
    order_mode = False
    for plan in plans:
        order_mode = order_mode or plan.mode == "order"
        print(f"target {plan.target.name}  ({plan.target.kind}, routing_mode={plan.mode})")
        resolved = f" -> {plan.resolved_model}" if plan.resolved_model != plan.model else ""
        print(f"  model    : {plan.model}{resolved}")
        mix = plan.mix
        print(f"  mix      : requests={mix['requests']} f={mix['f']:.4f} R={mix['R']:.5f} fw={mix['fw']:.5f}")
        print(f"  old order: {_order_text(plan.current_order)}")
        if plan.mode == "preferred":
            print("  new order: (none; preferred mode keeps sticky routing)")
            print(
                f"  proposed : sort={plan.proposed_routing.get('sort')} "
                f"ignore={plan.proposed_routing.get('ignore') or []} "
                f"preferred_min_throughput={plan.proposed_routing.get('preferred_min_throughput')} "
                f"preferred_max_latency={plan.proposed_routing.get('preferred_max_latency')}"
            )
        else:
            print(f"  new order: {_order_text(plan.proposed_order)}")
            old, new = set(plan.current_order), set(plan.proposed_order)
            added = [slug for slug in plan.proposed_order if slug not in old]
            removed = [slug for slug in plan.current_order if slug not in new]
            if added:
                print(f"  added    : {', '.join(added)}")
            if removed:
                print(f"  removed  : {', '.join(removed)}")
        if plan.rows:
            best = plan.rows[0]
            print(f"  C_eff    : {best.cost:.6f} USD/1M input on {best.slug}")
        if compare:
            summary = compare_modes(plan.rows, plan.mix)
            print(
                "  compare  : "
                f"order {summary['order']['endpoint']} ${summary['order']['estimated_cost_usd']:.6f} "
                f"vs preferred {summary['preferred']['endpoint']} "
                f"${summary['preferred']['estimated_cost_usd']:.6f} "
                f"({summary['delta_pct']:+.1f}%)"
            )
        print(f"  changed  : {'yes' if plan.changed else 'no'}")
        if plan.system_prompt:
            warn(
                f"target {plan.target.name}: the preset has a system_prompt; "
                "check the new version after applying"
            )
        print()
    if order_mode:
        warn("routing_mode=order disables OpenRouter sticky routing; use routing_mode=preferred to keep it")


def _print_refresh_result(result: RefreshResult) -> None:
    labels = _window_label(result.window)
    print(f"window   : {result.window_since or '?'} .. {result.window_until or '?'}  ({labels})")
    if not result.applied:
        print("dry run. re-run with --apply to write changed targets")
        return
    for plan in result.plans:
        if result.applied_targets.get(plan.target.name):
            print(f"applied {plan.target.name}")
        else:
            print(f"unchanged {plan.target.name}; no write")
    if result.state_path is not None:
        print(f"state    : {result.state_path}")


def cmd_refresh(args: Namespace) -> int:
    if args.apply and args.dry_run:
        raise ConfigError("use either --apply or --dry-run, not both")
    cfg = load_config(args.config)
    log_path = Path(args.log_file).expanduser() if args.log_file else cfg.refresh.log_file
    source = PiSessionSource(pattern=cfg.paths.sessions_glob)
    client = OpenRouterClient()
    with _optional_log(log_path) as log:
        try:
            result = run_refresh(
                cfg,
                source=source,
                client=client,
                target_names=args.target or (),
                hours=args.hours,
                days=args.days,
                since=args.since,
                until=args.until,
                anchor=args.anchor,
                apply=bool(args.apply),
                on_plan=lambda plans: _print_refresh_plans(plans, args.compare),
                log=log,
            )
        except AppError as exc:
            log(f"error: {exc}")
            raise
        _print_refresh_result(result)
    return 0


def _print_verify(result: VerifyResult) -> None:
    summary = result.summary
    print(
        f"{'when':19} {'provider':16} {'model':26} {'in':>8} {'cr':>8} {'cw':>5} {'out':>6} "
        f"{'pred $':>11} {'actual $':>11} {'delta':>9} status"
    )
    for check in result.checks:
        when = check.record.timestamp.strftime("%Y-%m-%d %H:%M:%S") if check.record.timestamp else "?"
        provider = (check.provider or "?")[:16]
        model = (check.model or check.record.routed_model or check.record.model)[:26]
        predicted = "-" if check.predicted_cost is None else f"{check.predicted_cost:.6f}"
        actual = "-" if check.actual_cost is None else f"{check.actual_cost:.6f}"
        delta = "-" if check.delta_usd is None else f"{check.delta_usd:+.6f}"
        print(
            f"{when:19} {provider:16} {model:26} {check.record.input_tokens:>8,} "
            f"{check.record.cache_read:>8,} {check.record.cache_write:>5,} {check.record.output_tokens:>6,} "
            f"{predicted:>11} {actual:>11} {delta:>9} {check.status}"
        )
        for flag in check.flags:
            print(f"    ! {flag}")
        if check.error:
            print(f"    ! {check.error}")
    print()
    print(
        f"generations: {summary.verified} verified, {summary.missing} missing/expired, "
        f"{summary.unmatched} unmatched, {summary.errors} error(s), {summary.mismatches} mismatch(es)"
    )
    print(
        f"tokens     : input {summary.input:,}  cacheRead {summary.cache_read:,}  "
        f"cacheWrite {summary.cache_write:,}  output {summary.output:,}"
    )
    print(f"mix        : f={summary.f:.4f}  R={summary.R:.5f}  fw={summary.fw:.5f}")
    if summary.actual_c_eff is not None and summary.predicted_c_eff is not None:
        print(
            f"cost       : predicted ${summary.predicted_cost:.6f}  actual ${summary.actual_cost:.6f}  "
            f"delta ${summary.delta_usd:+.6f}"
            + (f" ({summary.delta_pct:+.2f}%)" if summary.delta_pct is not None else "")
        )
        print(
            f"C_eff      : predicted ${summary.predicted_c_eff:.6f}  "
            f"actual ${summary.actual_c_eff:.6f} per 1M input tokens"
        )
    print(f"cache      : discount ${summary.cache_discount:.6f}")
    if summary.providers:
        providers = ", ".join(
            f"{name}={count}"
            for name, count in sorted(summary.providers.items(), key=lambda item: (-item[1], item[0]))
        )
        print(f"providers  : {providers}")
    if summary.flags:
        flags = ", ".join(
            f"{name} x{count}" for name, count in sorted(summary.flags.items(), key=lambda item: item[0])
        )
        print(f"flags      : {flags}")


def cmd_verify(args: Namespace) -> int:
    cfg = load_config(args.config)
    source = PiSessionSource(pattern=cfg.paths.sessions_glob)
    paths = source.discover()
    if not paths:
        raise ConfigError(f"no session files found under {cfg.paths.sessions_glob}")
    records = list(source.iter_records(paths))
    if not records:
        raise ConfigError("no usage records found in the selected session files")
    info(f"sessions   : {len(paths)} file(s), {len(records)} record(s)")

    last = args.last if args.last is not None else (None if args.since or args.until else DEFAULT_LAST)
    client = OpenRouterClient()
    result = verify_generations(
        records,
        client=client,
        last=last,
        since=args.since,
        until=args.until,
        models=args.model or (),
        routed_models=args.routed_model or (),
        exclude_models=args.exclude_model or (),
        providers=args.provider or (),
        projects=args.project or (),
        exclude_projects=args.exclude_project or (),
        tolerance=args.tolerance,
        progress=(lambda record: info(f"verify {record.response_id}")) if _VERBOSE else None,
    )
    summary = result.summary
    if summary.selected == 0:
        raise ConfigError("no session records with responseId matched the selection")

    if args.json:
        print(
            json.dumps(
                {
                    "schema": "openrouter-routing/verify/v1",
                    "filters": result.filters,
                    "summary": summary.as_dict(),
                    "records": [check.as_dict() for check in result.checks],
                },
                indent=2,
            )
        )
    else:
        _print_verify(result)

    if summary.missing:
        warn(f"{summary.missing} generation record(s) were not found or have expired")
    if summary.unmatched:
        warn(f"{summary.unmatched} provider(s) were not found in the endpoint list")
    if summary.mismatches:
        warn(f"{summary.mismatches} generation(s) differ from the predicted cost by more than tolerance")
    if summary.verified == 0:
        warn("no generation record could be verified")
        return 1
    if summary.errors:
        warn(f"{summary.errors} generation lookup(s) failed")
        return 1
    return 0


def _print_unit_preview(service_path: Path, timer_path: Path, service: str, timer: str) -> None:
    print(f"# {service_path}")
    print(service.rstrip())
    print()
    print(f"# {timer_path}")
    print(timer.rstrip())


def _print_commands(commands: Sequence[Sequence[str]]) -> None:
    for command in commands:
        print(f"ran {' '.join(command)}")


def cmd_install_systemd(args: Namespace) -> int:
    if args.mode != "daily":  # pragma: no cover - argparse restricts this today
        raise ConfigError(f"unsupported systemd mode: {args.mode}")
    if args.enable and args.print_only:
        raise ConfigError("use either --enable or --print, not both")
    if args.enable and (args.disable or args.remove):
        raise ConfigError("--enable cannot be combined with --disable/--remove")

    unit_dir = args.unit_dir
    if args.disable or args.remove:
        result = automation.uninstall_systemd(
            unit_dir=unit_dir,
            remove=bool(args.remove),
            print_only=bool(args.print_only),
        )
        if not result.existing:
            print(f"no openrouter-routing systemd units installed in {result.unit_dir}")
            return 0
        if args.print_only:
            print(f"would remove units from {result.unit_dir}" if args.remove else "would disable the timer")
            _print_commands(result.commands)
            print("dry run: no systemctl command run and no file removed")
            return 0
        _print_commands(result.commands)
        if result.disabled:
            print(f"disabled {automation.TIMER_NAME}")
        if result.removed:
            print(f"removed {result.service_path}")
            print(f"removed {result.timer_path}")
        else:
            print("unit files kept; add --remove to delete them")
        return 0

    cfg = load_config(args.config)
    config_path = cfg.source_path or default_config_path()
    if not cfg.loaded:
        warn(f"{config_path} does not exist; refresh will use defaults")
    elif not cfg.targets:
        warn("config has no [[target]] blocks; refresh will fail until one is configured")

    print_only = not args.enable
    result = automation.install_systemd(
        config_path=config_path,
        unit_dir=unit_dir,
        binary=args.binary,
        force=bool(args.force),
        enable=bool(args.enable),
        print_only=print_only,
    )
    if print_only:
        _print_unit_preview(result.service_path, result.timer_path, result.service_text, result.timer_text)
        print()
        print("dry run: no files written and no systemctl command run")
        print("re-run with --enable to write the units and enable the timer")
        tip("note: `loginctl enable-linger $USER` lets the timer run while you are logged out")
        return 0

    print(f"wrote {result.service_path}")
    print(f"wrote {result.timer_path}")
    _print_commands(result.commands)
    print(
        "timer enabled: login triggers a refresh after ~2min; long sessions refresh daily; "
        "Persistent=true catches missed runs"
    )
    tip("note: `loginctl enable-linger $USER` lets the timer run while you are logged out")
    return 0


def cmd_doctor(args: Namespace) -> int:
    cfg = load_config(args.config)
    source = PiSessionSource(pattern=cfg.paths.sessions_glob)
    client = OpenRouterClient()
    checks = run_doctor(cfg, client=client, source=source)
    for check in checks:
        print(format_check(check))
    bad = sum(1 for check in checks if not check.ok)
    warnings = sum(1 for check in checks if check.ok and check.warning)
    print(f"\n{len(checks)} check(s): {bad} failed, {warnings} warning(s)")
    return 1 if doctor_failed(checks) else 0


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
    cheapest.add_argument(
        "model", nargs="?", help="model slug, e.g. deepseek/deepseek-chat-v3.1 (optional with --preset)"
    )
    cheapest.add_argument("--preset", help="preset slug whose model/current order should be compared")
    cheapest.add_argument(
        "--compare-current",
        action="store_true",
        help="compare the preset's current provider.order with the proposed best endpoint",
    )
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
    plan.add_argument(
        "--routing-mode",
        choices=["order", "preferred"],
        default=None,
        help="order pins endpoints; preferred keeps sticky routing (default order)",
    )
    plan.add_argument("--ignore", default=None, help="comma separated provider slugs to ignore")
    plan.add_argument("--allow-fallbacks", dest="allow_fallbacks", action="store_true", default=None)
    plan.add_argument("--no-allow-fallbacks", dest="allow_fallbacks", action="store_false")
    _add_rank_args(plan, preset=True)
    plan.add_argument("--apply", action="store_true", help="POST the new config (creates a version)")
    plan.add_argument("--dry-run", action="store_true", help="explicitly keep the current preset (default)")
    plan.add_argument("--force", action="store_true", help="apply even when provider.order is unchanged")
    plan.set_defaults(func=cmd_preset_plan)

    rollback = preset_sub.add_parser("rollback", help="re-POST the config of an older version")
    _add_config_arg(rollback)
    _add_output_args(rollback)
    rollback.add_argument("slug")
    rollback.add_argument("--version", type=int, required=True)
    rollback.add_argument("--apply", action="store_true")
    rollback.add_argument(
        "--dry-run", action="store_true", help="explicitly keep the current preset (default)"
    )
    rollback.set_defaults(func=cmd_preset_rollback)

    refresh = sub.add_parser("refresh", help="measure a window and refresh configured routing targets")
    _add_config_arg(refresh)
    _add_output_args(refresh)
    refresh.add_argument("--target", action="append", help="target name from config (repeatable)")
    refresh.add_argument("--hours", type=float, default=None, help="override the refresh window")
    refresh.add_argument("--days", type=float, default=None, help="override the refresh window")
    refresh.add_argument("--since", help="only entries at/after this ISO timestamp")
    refresh.add_argument("--until", help="only entries at/before this ISO timestamp")
    refresh.add_argument(
        "--anchor",
        choices=["latest", "now"],
        default=None,
        help="relative window anchor (refresh default: now)",
    )
    refresh.add_argument("--apply", action="store_true", help="write changed targets")
    refresh.add_argument("--dry-run", action="store_true", help="explicitly keep targets unchanged (default)")
    refresh.add_argument("--compare", action="store_true", help="estimate order vs preferred routing")
    refresh.add_argument("--log-file", help="append refresh diagnostics to this file")
    refresh.set_defaults(func=cmd_refresh)

    verify = sub.add_parser("verify", help="compare predicted cost with OpenRouter generation records")
    _add_config_arg(verify)
    _add_output_args(verify)
    verify.add_argument(
        "--last",
        type=int,
        default=None,
        metavar="N",
        help=f"verify the N most recent generations (default {DEFAULT_LAST} when no --since/--until)",
    )
    verify.add_argument("--since", help="only generations at/after this ISO timestamp (inclusive)")
    verify.add_argument("--until", help="only generations at/before this ISO timestamp (inclusive)")
    verify.add_argument("--model", action="append", help="filter requested model (repeatable)")
    verify.add_argument("--routed-model", action="append", help="filter upstream responseModel (repeatable)")
    verify.add_argument("--exclude-model", action="append", help="exclude requested model (repeatable)")
    verify.add_argument("--provider", action="append", help="filter pi provider (repeatable)")
    verify.add_argument("--project", action="append", help="session cwd root (repeatable, recursive)")
    verify.add_argument(
        "--exclude-project", action="append", help="exclude a session cwd root (repeatable, recursive)"
    )
    verify.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        metavar="FRACTION",
        help=f"relative cost mismatch tolerance (default {DEFAULT_TOLERANCE})",
    )
    verify.add_argument("--json", action="store_true", help="machine-readable verification report")
    verify.set_defaults(func=cmd_verify)

    automation_parser = sub.add_parser(
        "install-systemd",
        help="preview, install, disable, or remove the opt-in systemd user timer",
    )
    _add_config_arg(automation_parser)
    _add_output_args(automation_parser)
    automation_parser.add_argument("--mode", choices=["daily"], default="daily", help="unit mode (daily)")
    automation_parser.add_argument(
        "--enable",
        action="store_true",
        help="write the units, run daemon-reload, and enable --now the timer",
    )
    automation_parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="print the units and commands without writing or enabling",
    )
    automation_parser.add_argument("--force", action="store_true", help="overwrite existing unit files")
    automation_parser.add_argument(
        "--binary",
        help="ExecStart binary (default: openrouter-routing from PATH, then the local venv/-m fallback)",
    )
    automation_parser.add_argument(
        "--unit-dir",
        help="unit directory (default: ${XDG_CONFIG_HOME:-~/.config}/systemd/user)",
    )
    automation_parser.add_argument(
        "--disable",
        action="store_true",
        help="disable --now the timer (add --remove to delete the unit files)",
    )
    automation_parser.add_argument(
        "--remove",
        action="store_true",
        help="disable and remove the unit files (implies --disable)",
    )
    automation_parser.set_defaults(func=cmd_install_systemd)

    doctor = sub.add_parser("doctor", help="check config, sessions, credentials, network, and targets")
    _add_config_arg(doctor)
    _add_output_args(doctor)
    doctor.set_defaults(func=cmd_doctor)

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
    config_init.add_argument("--dry-run", action="store_true", help="print the path without writing")
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
    except AppError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
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
