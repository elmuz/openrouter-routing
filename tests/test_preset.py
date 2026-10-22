from __future__ import annotations

from argparse import Namespace

import pytest

from openrouter_routing.errors import PlanError
from openrouter_routing.preset import new_config


def endpoint(tag: str, prompt: str, completion: str, cr: str | None, *, status: int = 0, tps: float = 100):
    pricing = {"prompt": prompt, "completion": completion}
    if cr is not None:
        pricing["input_cache_read"] = cr
        pricing["input_cache_write"] = "0"
    return {
        "tag": tag,
        "provider_name": tag.split("/")[0],
        "pricing": pricing,
        "quantization": "fp8",
        "context_length": 100000,
        "status": status,
        "uptime_last_1d": 99.0,
        "latency_last_30m": {"p90": 1000},
        "throughput_last_30m": {"p90": tps},
    }


class FakeClient:
    def __init__(self, endpoints, catalog):
        self.endpoints = endpoints
        self.catalog = catalog

    def fetch_endpoints(self, model):
        return {"endpoints": self.endpoints.get(model, [])}

    def fetch_models_catalog(self):
        return self.catalog


def options(**overrides) -> Namespace:
    base = {
        "model": None,
        "sort": None,
        "ignore": None,
        "quant": None,
        "allow_fallbacks": None,
        "require_cache": False,
        "min_tps": None,
        "max_ttft": None,
        "tps_pct": "p90",
        "ttft_pct": "p90",
        "prefer": False,
        "top": None,
        "routing_mode": "order",
    }
    base.update(overrides)
    return Namespace(**base)


def test_new_config_preserves_foreign_fields_and_resolves_alias() -> None:
    current = {
        "model": "~vendor/model-latest",
        "temperature": 0.2,
        "provider": {
            "sort": {"by": "price", "partition": None},
            "order": ["old"],
            "ignore": ["open-inference"],
            "quantizations": [],
            "allow_fallbacks": True,
        },
    }
    client = FakeClient(
        {
            "vendor/model-1": [
                endpoint("good/fp8", "0.000001", "0.000002", "0.0000001"),
                endpoint("slow/fp8", "0.0000001", "0.0000002", "0.00000001", tps=5),
                endpoint("bad/fp8", "0", "0", "0", status=-1),
                endpoint("open-inference/fp8", "0.0000001", "0.0000002", "0.00000001"),
            ]
        },
        [{"id": "~vendor/model-latest", "alias_target": {"slug": "vendor/model-1"}}],
    )
    cfg, rows, resolved = new_config(
        current,
        {"f": 0.9, "R": 0.01, "fw": 0.0},
        options(min_tps=40, top=1),
        client,
    )
    assert resolved == "vendor/model-1"
    assert cfg["model"] == "~vendor/model-latest"
    assert cfg["temperature"] == 0.2
    assert cfg["provider"]["sort"] == {"by": "price", "partition": None}
    assert cfg["provider"]["ignore"] == ["open-inference"]
    assert cfg["provider"]["order"] == ["good/fp8"]
    assert [row.slug for row in rows] == ["good/fp8"]


def test_new_config_applies_overrides_and_preferences() -> None:
    current = {"model": "vendor/model-1", "provider": {}}
    client = FakeClient(
        {"vendor/model-1": [endpoint("good/fp8", "0.000001", "0.000002", "0.0000001")]},
        [],
    )
    cfg, _, _ = new_config(
        current,
        {"f": 0.9, "R": 0.01, "fw": 0.0},
        options(
            sort="throughput",
            ignore="bad",
            quant="fp8",
            allow_fallbacks=False,
            prefer=True,
            min_tps=40,
            max_ttft=6,
        ),
        client,
    )
    assert cfg["provider"]["sort"] == {"by": "throughput", "partition": None}
    assert cfg["provider"]["ignore"] == ["bad"]
    assert cfg["provider"]["quantizations"] == ["fp8"]
    assert cfg["provider"]["allow_fallbacks"] is False
    assert cfg["provider"]["preferred_min_throughput"] == {"p90": 40}
    assert cfg["provider"]["preferred_max_latency"] == {"p90": 6}


def test_new_config_preferred_mode_keeps_soft_floors_and_sticky_routing() -> None:
    current = {
        "model": "vendor/model-1",
        "provider": {"order": ["old"], "sort": {"by": "throughput", "partition": "none"}},
    }
    client = FakeClient(
        {
            "vendor/model-1": [
                endpoint("good/fp8", "0.000001", "0.000002", "0.0000001"),
                endpoint("slow/fp8", "0.0000001", "0.0000002", "0.00000001", tps=5),
            ]
        },
        [],
    )
    cfg, rows, _ = new_config(
        current,
        {"f": 0.9, "R": 0.01, "fw": 0.0},
        options(
            routing_mode="preferred",
            min_tps=40,
            max_ttft=6,
            top=1,
            prefer=False,
        ),
        client,
    )
    provider = cfg["provider"]
    assert "order" not in provider
    assert provider["sort"] == {"by": "price", "partition": "none"}
    assert provider["preferred_min_throughput"] == {"p90": 40}
    assert provider["preferred_max_latency"] == {"p90": 6}
    # Throughput/latency are soft in preferred mode, so the slow endpoint survives.
    assert {row.slug for row in rows} == {"good/fp8", "slow/fp8"}


def test_new_config_rejects_unknown_mode() -> None:
    client = FakeClient({"vendor/model-1": [endpoint("a/fp8", "0.000001", "0.000002", "0.0000001")]}, [])
    with pytest.raises(PlanError, match="unknown routing mode"):
        new_config(
            {"model": "vendor/model-1", "provider": {}},
            {},
            options(routing_mode="sticky"),
            client,
        )


def test_new_config_raises_when_nothing_is_left() -> None:
    client = FakeClient({"vendor/model-1": [endpoint("bad/fp8", "0", "0", "0", status=-1)]}, [])
    with pytest.raises(PlanError):
        new_config({"model": "vendor/model-1", "provider": {}}, {}, options(), client)
