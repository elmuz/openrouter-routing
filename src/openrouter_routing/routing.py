from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .errors import PlanError
from .pricing import EndpointScore, estimated_cost, pct_stat


@dataclass(frozen=True)
class RankResult:
    rows: list[EndpointScore]
    best: EndpointScore
    known_perf: int


def filter_scores(
    rows: Iterable[EndpointScore],
    *,
    healthy_only: bool = False,
    require_cache: bool = False,
    quant: str | None = None,
    min_tps: float | None = None,
    max_ttft: float | None = None,
    tps_pct: str = "p90",
    ttft_pct: str = "p90",
) -> list[EndpointScore]:
    selected = list(rows)
    if healthy_only:
        selected = [row for row in selected if row.status == 0]
    if require_cache:
        selected = [row for row in selected if row.cached]
    if quant:
        keep = {item.strip() for item in quant.split(",") if item.strip()}
        selected = [row for row in selected if row.quant in keep]
    if min_tps is not None:
        selected = [row for row in selected if (pct_stat(row.tps, tps_pct) or 0) >= min_tps]
    if max_ttft is not None:
        # latency_last_30m is milliseconds, preferred_max_latency is seconds
        selected = [
            row for row in selected if (pct_stat(row.ttft, ttft_pct) or float("inf")) <= max_ttft * 1000
        ]
    return selected


def rank(
    rows: Iterable[EndpointScore],
    **filters: Any,
) -> RankResult:
    candidates = list(rows)
    known_perf = sum(
        1
        for row in candidates
        if pct_stat(row.tps, filters.get("tps_pct", "p90")) is not None
        or pct_stat(row.ttft, filters.get("ttft_pct", "p90")) is not None
    )
    selected = filter_scores(candidates, **filters)
    selected.sort(key=lambda row: row.cost)
    if not selected:
        raise PlanError("no endpoints left after filtering")
    return RankResult(rows=selected, best=selected[0], known_perf=known_perf)


def emittable(rows: Iterable[EndpointScore]) -> tuple[list[EndpointScore], list[EndpointScore]]:
    """Split rows into safe-to-pin (status == 0) and degraded/deprecated."""
    safe: list[EndpointScore] = []
    dropped: list[EndpointScore] = []
    for row in rows:
        (safe if row.status == 0 else dropped).append(row)
    return safe, dropped


def compare_modes(rows: Iterable[EndpointScore], mix: dict[str, Any]) -> dict[str, Any]:
    """Estimate a mix under `order` and `sort: price` routing.

    `order` uses the lowest C_eff endpoint. `preferred` approximates OpenRouter's
    `sort: price` by minimizing uncached input plus output cost (cache-read
    pricing is invisible to that sort); sticky routing then keeps the measured
    cache-hit share. The exact sort tie-breaking is not public, so this is an
    estimate, not a billing prediction.
    """
    candidates = [row for row in rows if row.slug and row.status == 0]
    if not candidates:
        raise PlanError("no healthy endpoint available for a mode comparison")
    r = float(mix.get("R") or 0.0)
    ordered = min(candidates, key=lambda row: (row.cost, row.slug))
    sorted_by_price = min(
        candidates,
        key=lambda row: (row.in_price + r * row.out_price, row.cost, row.slug),
    )
    order_cost = estimated_cost(mix, ordered)
    preferred_cost = estimated_cost(mix, sorted_by_price)
    delta = preferred_cost - order_cost
    return {
        "order": {
            "endpoint": ordered.slug,
            "c_eff": ordered.cost,
            "estimated_cost_usd": order_cost,
        },
        "preferred": {
            "endpoint": sorted_by_price.slug,
            "c_eff": sorted_by_price.cost,
            "estimated_cost_usd": preferred_cost,
        },
        "delta_usd": delta,
        "delta_pct": (delta / order_cost * 100) if order_cost else 0.0,
    }


def provider_block(
    rows: list[EndpointScore],
    *,
    max_price_from: int = 0,
    prefer: bool = False,
    min_tps: float | None = None,
    max_ttft: float | None = None,
    tps_pct: str = "p90",
    ttft_pct: str = "p90",
) -> dict[str, Any]:
    safe, _ = emittable(rows)
    if not safe:
        raise PlanError("every endpoint has status != 0; nothing safe to pin")
    cap_index = min(max_price_from, len(safe) - 1)
    cap = safe[cap_index]
    block: dict[str, Any] = {
        "order": [row.slug for row in safe],
        "allow_fallbacks": True,
        "max_price": {"prompt": round(cap.in_price, 6), "completion": round(cap.out_price, 6)},
    }
    if prefer:
        if min_tps is not None:
            block["preferred_min_throughput"] = {tps_pct: min_tps}
        if max_ttft is not None:
            block["preferred_max_latency"] = {ttft_pct: max_ttft}
    return {"provider": block}


def first_ordered_endpoint(rows: Iterable[EndpointScore], order: Iterable[str]) -> EndpointScore | None:
    """Return the first endpoint in a preset's order that still exists."""
    by_slug = {row.slug: row for row in rows}
    for slug in order:
        row = by_slug.get(slug)
        if row is not None:
            return row
    return None


COMPARE_CAVEAT = (
    "Rough estimate, not a prediction: both models are scored with the same token "
    "mix (f/R/fw) and the same token counts. It assumes their tokenizers produce "
    "the same number of tokens for your text, that prompt caching behaves the same "
    "way (TTL, minimum prompt size), and that output length is unchanged. It does "
    "not price quality, retries, or context-window limits, and prices are a "
    "snapshot that can change."
)


def _best_for(model: str, rows: Iterable[EndpointScore], filters: dict[str, Any]) -> EndpointScore:
    try:
        return rank(rows, **filters).best
    except PlanError as exc:
        raise PlanError(f"{model}: {exc}") from exc


def compare_models(
    model_a: str,
    rows_a: Iterable[EndpointScore],
    model_b: str,
    rows_b: Iterable[EndpointScore],
    counters: dict[str, Any],
    **filters: Any,
) -> dict[str, Any]:
    """Compare the cheapest endpoint of two models under one token mix.

    `counters` is the shared workload (input/cacheRead/cacheWrite/output token
    counts). Both models are ranked with the same filters and priced with the
    same counters, so the result answers "which model is cheaper for the work I
    actually do", not "which model did I use more".
    """
    best_a = _best_for(model_a, rows_a, filters)
    best_b = _best_for(model_b, rows_b, filters)
    cost_a = estimated_cost(counters, best_a)
    cost_b = estimated_cost(counters, best_b)

    def entry(model: str, row: EndpointScore, cost: float) -> dict[str, Any]:
        return {
            "model": model,
            "endpoint": row.slug,
            "c_eff": row.cost,
            "estimated_cost_usd": cost,
            "f_used": row.f_used,
            "cached": row.cached,
            "ctx": row.ctx,
            "quant": row.quant,
            "status": row.status,
            "uptime": row.uptime,
        }

    if cost_a < cost_b:
        cheaper = "a"
    elif cost_b < cost_a:
        cheaper = "b"
    else:
        cheaper = "tie"
    high = max(cost_a, cost_b)
    low = min(cost_a, cost_b)
    delta = high - low
    return {
        "a": entry(model_a, best_a, cost_a),
        "b": entry(model_b, best_b, cost_b),
        "cheaper": cheaper,
        "cheaper_model": model_a if cheaper == "a" else model_b if cheaper == "b" else None,
        "delta_usd": delta,
        "delta_pct": (delta / high * 100) if high else 0.0,
        "ratio": (high / low) if low else None,
        "caveat": COMPARE_CAVEAT,
    }


def compare_current(
    rows: Iterable[EndpointScore],
    current_order: Iterable[str],
    mix: dict[str, Any],
    *,
    proposed: EndpointScore | None = None,
) -> dict[str, Any]:
    """Compare a preset's current order with the best endpoint for this mix."""
    candidates = [row for row in rows if row.slug]
    if not candidates:
        raise PlanError("no endpoints available for a current-order comparison")
    best = proposed or min(candidates, key=lambda row: (row.cost, row.slug))
    order = list(current_order)
    current = first_ordered_endpoint(candidates, order)

    def entry(row: EndpointScore | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "endpoint": row.slug,
            "c_eff": row.cost,
            "estimated_cost_usd": estimated_cost(mix, row),
        }

    current_entry = entry(current)
    proposed_entry = entry(best)
    if current_entry is not None:
        delta = proposed_entry["estimated_cost_usd"] - current_entry["estimated_cost_usd"]
        base = current_entry["estimated_cost_usd"]
        delta_pct = (delta / base * 100) if base else 0.0
    else:
        delta = None
        delta_pct = None
    return {
        "current_order": order,
        "current": current_entry,
        "proposed": proposed_entry,
        "delta_usd": delta,
        "delta_pct": delta_pct,
    }
