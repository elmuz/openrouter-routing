from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .errors import PlanError
from .pricing import EndpointScore, pct_stat


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
