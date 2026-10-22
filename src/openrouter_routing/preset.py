from __future__ import annotations

import copy
from argparse import Namespace
from typing import Any

from .api import OpenRouterClient, resolve_model
from .errors import PlanError
from .pricing import EndpointScore, pct_stat, score_endpoint


def model_of(config: dict[str, Any]) -> str:
    if config.get("model"):
        return str(config["model"])
    models = config.get("models") or []
    if not models:
        raise PlanError("preset config has no model")
    return str(models[0])


def cfg_provider(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("provider") or {}


def _provider_filter(
    rows: list[EndpointScore],
    *,
    ignored: set[str],
    quant_ok: set[str],
    require_cache: bool,
    min_tps: float | None,
    max_ttft: float | None,
    tps_pct: str,
    ttft_pct: str,
    hard_perf: bool = True,
) -> list[EndpointScore]:
    rows = [row for row in rows if row.slug and row.status == 0]
    if ignored:
        rows = [row for row in rows if row.slug.split("/")[0] not in ignored]
    if quant_ok:
        rows = [row for row in rows if row.quant in quant_ok]
    if require_cache:
        rows = [row for row in rows if row.cached]
    if hard_perf and min_tps is not None:
        rows = [row for row in rows if (pct_stat(row.tps, tps_pct) or 0) >= min_tps]
    if hard_perf and max_ttft is not None:
        rows = [row for row in rows if (pct_stat(row.ttft, ttft_pct) or float("inf")) <= max_ttft * 1000]
    return rows


def new_config(
    current: dict[str, Any],
    mix: dict[str, Any],
    options: Namespace,
    client: OpenRouterClient,
) -> tuple[dict[str, Any], list[EndpointScore], str]:
    """Return (config, ranked rows, resolved model) for a preset plan."""
    cfg = copy.deepcopy(current)
    prov = cfg.setdefault("provider", {})
    model = options.model or model_of(cfg)

    if options.sort:
        cur = prov.get("sort") or {"by": "price", "partition": None}
        if isinstance(cur, str):
            cur = {"by": cur, "partition": None}
        cur["by"] = options.sort
        prov["sort"] = cur
    if options.ignore is not None:
        prov["ignore"] = [item for item in options.ignore.split(",") if item]
    if options.quant is not None:
        prov["quantizations"] = [item for item in options.quant.split(",") if item]
    if options.allow_fallbacks is not None:
        prov["allow_fallbacks"] = options.allow_fallbacks

    mode = getattr(options, "routing_mode", None) or "order"
    if mode not in {"order", "preferred"}:
        raise PlanError(f"unknown routing mode: {mode}")

    f = float(mix.get("f", 0.0))
    r = float(mix.get("R", 0.0))
    fw = float(mix.get("fw", 0.0))
    resolved, endpoints = resolve_model(model, client)
    rows = [score_endpoint(endpoint, f, r, fw) for endpoint in endpoints]
    rows = _provider_filter(
        rows,
        ignored=set(prov.get("ignore") or []),
        quant_ok=set(prov.get("quantizations") or []),
        require_cache=options.require_cache,
        min_tps=options.min_tps,
        max_ttft=options.max_ttft,
        tps_pct=options.tps_pct,
        ttft_pct=options.ttft_pct,
        # In preferred mode throughput/latency become soft preferred_* floors.
        hard_perf=mode == "order",
    )
    if not rows:
        raise PlanError(
            f"{model}: no healthy endpoint left after the preset's own filters "
            f"(ignore={sorted(prov.get('ignore') or [])}, "
            f"quantizations={sorted(prov.get('quantizations') or [])})"
        )

    rows.sort(key=lambda row: row.cost)
    all_rows = rows
    if mode == "preferred":
        # Keep OpenRouter's sticky cache routing: no manual order, sort by the
        # plain prompt/completion price, and use soft preferred_* floors.
        prov.pop("order", None)
        current_sort = prov.get("sort")
        partition = current_sort.get("partition") if isinstance(current_sort, dict) else None
        prov["sort"] = {"by": "price", "partition": partition}
        if options.min_tps is not None:
            prov["preferred_min_throughput"] = {options.tps_pct: options.min_tps}
        if options.max_ttft is not None:
            prov["preferred_max_latency"] = {options.ttft_pct: options.max_ttft}
    else:
        if options.prefer:
            if options.min_tps is not None:
                prov["preferred_min_throughput"] = {options.tps_pct: options.min_tps}
            if options.max_ttft is not None:
                prov["preferred_max_latency"] = {options.ttft_pct: options.max_ttft}
        if options.top:
            rows = rows[: options.top]
        prov["order"] = [row.slug for row in rows]
        if getattr(options, "keep_all_rows", False):
            rows = all_rows

    cfg["model"] = model
    return cfg, rows, resolved
