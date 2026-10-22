from __future__ import annotations

import pytest

from openrouter_routing.errors import PlanError
from openrouter_routing.pricing import EndpointScore
from openrouter_routing.routing import emittable, filter_scores, provider_block, rank


def row(
    slug: str,
    cost: float,
    *,
    status: int = 0,
    cached: bool = True,
    tps90: float | None = 60,
    ttft90: float | None = 1000,
    quant: str = "fp8",
) -> EndpointScore:
    return EndpointScore(
        slug=slug,
        provider=slug.split("/")[0],
        in_price=1.0,
        out_price=2.0,
        cr_price=0.1 if cached else None,
        cw_price=0.0 if cached else None,
        cached=cached,
        implicit_cache=cached,
        f_used=0.9 if cached else 0,
        cost=cost,
        quant=quant,
        ctx=100000,
        status=status,
        uptime=99.0,
        tps={"p90": tps90} if tps90 is not None else {},
        ttft={"p90": ttft90} if ttft90 is not None else {},
    )


def test_filter_scores() -> None:
    rows = [
        row("a/fp8", 1.0),
        row("b/fp8", 2.0, tps90=10),
        row("c/fp8", 3.0, status=-5),
        row("d/fp4", 4.0, cached=False, quant="fp4"),
    ]
    assert [r.slug for r in filter_scores(rows, healthy_only=True)] == ["a/fp8", "b/fp8", "d/fp4"]
    assert [r.slug for r in filter_scores(rows, require_cache=True)] == ["a/fp8", "b/fp8", "c/fp8"]
    assert [r.slug for r in filter_scores(rows, quant="fp4")] == ["d/fp4"]
    assert [r.slug for r in filter_scores(rows, min_tps=40, tps_pct="p90")] == ["a/fp8", "c/fp8", "d/fp4"]


def test_rank_sorts_and_reports() -> None:
    result = rank([row("b/fp8", 2.0), row("a/fp8", 1.0)], healthy_only=True)
    assert result.best.slug == "a/fp8"
    assert [r.slug for r in result.rows] == ["a/fp8", "b/fp8"]
    assert result.known_perf == 2


def test_rank_empty_raises() -> None:
    with pytest.raises(PlanError):
        rank([row("a/fp8", 1.0)], require_cache=True, quant="fp4")


def test_emittable_and_provider_block() -> None:
    rows = [row("a/fp8", 1.0), row("bad/fp8", 2.0, status=-1)]
    safe, dropped = emittable(rows)
    assert [r.slug for r in safe] == ["a/fp8"]
    assert [r.slug for r in dropped] == ["bad/fp8"]
    block = provider_block(rows, prefer=True, min_tps=40, max_ttft=6)
    assert block["provider"]["order"] == ["a/fp8"]
    assert block["provider"]["preferred_min_throughput"] == {"p90": 40}
    assert block["provider"]["preferred_max_latency"] == {"p90": 6}


def test_provider_block_no_safe_endpoint() -> None:
    with pytest.raises(PlanError):
        provider_block([row("bad/fp8", 1.0, status=-1)])
