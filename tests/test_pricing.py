from __future__ import annotations

import pytest

from openrouter_routing.pricing import pct_stat, price, score_endpoint


def endpoint(**overrides):
    base = {
        "tag": "vendor/fp8",
        "provider_name": "Vendor",
        "pricing": {
            "prompt": "0.000001",
            "completion": "0.000002",
            "input_cache_read": "0.0000001",
            "input_cache_write": "0.00000125",
        },
        "quantization": "fp8",
        "context_length": 100000,
        "status": 0,
        "uptime_last_1d": 99.5,
        "latency_last_30m": {"p50": 500, "p90": 900},
        "throughput_last_30m": {"p50": 80, "p90": 60},
        "supports_implicit_caching": True,
    }
    base.update(overrides)
    return base


def test_price_converts_to_per_million() -> None:
    assert price({"prompt": "0.000001"}, "prompt") == pytest.approx(1.0)
    assert price({}, "prompt") is None
    assert price({"prompt": ""}, "prompt") is None
    assert price({"prompt": "bad"}, "prompt") is None


def test_score_cached_endpoint() -> None:
    row = score_endpoint(endpoint(), f=0.9, r=0.01, fw=0.1)
    assert row.slug == "vendor/fp8"
    assert row.cached is True
    assert row.f_used == 0.9
    assert row.cost == pytest.approx(0.1 * 1 + 0.9 * 0.1 + 0.01 * 2 + 0.1 * 1.25)


def test_score_no_cache_endpoint_collapses_f() -> None:
    row = score_endpoint(
        endpoint(pricing={"prompt": "0.000001", "completion": "0.000002"}),
        f=0.9,
        r=0.01,
        fw=0.1,
    )
    assert row.cached is False
    assert row.f_used == 0.0
    assert row.cost == pytest.approx(1.0 + 0.01 * 2)


def test_pct_stat_and_as_dict() -> None:
    row = score_endpoint(endpoint(), 0.5, 0.01, 0)
    assert pct_stat(row.tps, "p90") == 60
    assert pct_stat(None, "p90") is None
    blob = row.as_dict()
    assert blob["in"] == pytest.approx(1.0)
    assert blob["cr"] == pytest.approx(0.1)
