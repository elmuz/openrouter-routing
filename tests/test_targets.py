from __future__ import annotations

import json
from pathlib import Path

import pytest

from openrouter_routing.config import RankConfig, TargetConfig
from openrouter_routing.errors import ConfigError, PlanError
from openrouter_routing.targets import PiModelTarget, PresetTarget, open_target


def endpoint(tag: str, prompt: str, completion: str, cr: str, *, tps: float = 100):
    return {
        "tag": tag,
        "provider_name": tag.split("/")[0],
        "pricing": {
            "prompt": prompt,
            "completion": completion,
            "input_cache_read": cr,
            "input_cache_write": "0",
        },
        "quantization": "fp8",
        "context_length": 100000,
        "status": 0,
        "uptime_last_1d": 99.0,
        "latency_last_30m": {"p90": 1000},
        "throughput_last_30m": {"p90": tps},
    }


class FakeClient:
    def __init__(self, endpoints: dict[str, list[dict]], preset: dict | None = None) -> None:
        self.endpoints = endpoints
        self.preset = preset
        self.posted: list[tuple[str, dict]] = []

    def fetch_endpoints(self, model: str):
        return {"endpoints": self.endpoints.get(model, [])}

    def fetch_models_catalog(self):
        return []

    def get_preset(self, slug: str):
        assert self.preset is not None
        return self.preset

    def post_preset_config(self, slug: str, config: dict):
        self.posted.append((slug, config))
        return {"data": {}}


def preset_payload(config: dict) -> dict:
    return {
        "slug": "p",
        "name": "P",
        "status": "active",
        "designated_version": {"version": 3, "config": config, "system_prompt": ""},
    }


def test_preset_target_plans_preserves_fields_and_applies_once() -> None:
    current = {
        "model": "vendor/model-1",
        "temperature": 0.2,
        "provider": {
            "sort": {"by": "price", "partition": None},
            "order": ["old/fp8"],
            "ignore": ["bad"],
            "quantizations": [],
            "allow_fallbacks": True,
        },
    }
    client = FakeClient(
        {
            "vendor/model-1": [
                endpoint("a/fp8", "0.000001", "0.000002", "0.0000001"),
                endpoint("b/fp8", "0.000002", "0.000003", "0.0000002"),
            ]
        },
        preset_payload(current),
    )
    target = TargetConfig(name="t", kind="preset", preset="p")
    backend = PresetTarget(target, client)
    plan = backend.plan({"f": 0.9, "R": 0.01, "fw": 0.0}, RankConfig(top=1), "order")

    assert plan.current_order == ("old/fp8",)
    assert plan.proposed_order == ("a/fp8",)
    # The full set is retained for --compare even though top=1 pins only one.
    assert len(plan.rows) == 2
    assert plan.changed is True
    assert plan.payload["temperature"] == 0.2
    assert plan.payload["provider"]["ignore"] == ["bad"]
    assert plan.payload["provider"]["order"] == ["a/fp8"]

    assert backend.apply(plan) is None
    assert client.posted == [("p", plan.payload)]


def test_preset_target_noop_does_not_post() -> None:
    current = {"model": "vendor/model-1", "provider": {"order": ["a/fp8"]}}
    client = FakeClient(
        {"vendor/model-1": [endpoint("a/fp8", "0.000001", "0.000002", "0.0000001")]},
        preset_payload(current),
    )
    backend = PresetTarget(TargetConfig(name="t", kind="preset", preset="p"), client)
    plan = backend.plan({"f": 0.9, "R": 0.01, "fw": 0.0}, RankConfig(), "order")
    assert plan.changed is False
    assert backend.apply(plan) is None
    assert client.posted == []


def test_preset_target_requires_slug_and_config() -> None:
    with pytest.raises(ConfigError, match="requires preset"):
        PresetTarget(TargetConfig(name="t"), FakeClient({}))
    with pytest.raises(PlanError, match="no config"):
        PresetTarget(
            TargetConfig(name="t", preset="p"),
            FakeClient({}, {"designated_version": {"config": {}}}),
        )


def test_preferred_mode_drops_order_and_keeps_soft_floors() -> None:
    current = {
        "model": "vendor/model-1",
        "provider": {"order": ["a/fp8"], "sort": {"by": "throughput", "partition": "none"}},
    }
    client = FakeClient(
        {
            "vendor/model-1": [
                endpoint("a/fp8", "0.000001", "0.000002", "0.0000001"),
                endpoint("slow/fp8", "0.0000001", "0.0000002", "0.00000001", tps=5),
            ]
        },
        preset_payload(current),
    )
    backend = PresetTarget(TargetConfig(name="t", kind="preset", preset="p"), client)
    plan = backend.plan(
        {"f": 0.9, "R": 0.01, "fw": 0.0},
        RankConfig(min_tps=40, max_ttft=6),
        "preferred",
    )
    provider = plan.payload["provider"]
    assert "order" not in provider
    assert provider["sort"] == {"by": "price", "partition": "none"}
    assert provider["preferred_min_throughput"] == {"p90": 40}
    assert provider["preferred_max_latency"] == {"p90": 6}
    assert "slow/fp8" in {row.slug for row in plan.rows}
    assert plan.proposed_order == ()


def test_pi_model_target_preserves_unknown_fields_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    original = {
        "$schema": "https://pi.dev/schemas/models.schema.json",
        "providers": {
            "openrouter": {
                "baseUrl": "https://openrouter.ai/api/v1",
                "models": [
                    {
                        "id": "@preset/ds4-flash",
                        "contextWindow": 1000000,
                        "compat": {
                            "openRouterRouting": {
                                "order": ["old/fp8"],
                                "allow_fallbacks": True,
                            },
                            "keepMe": True,
                        },
                    },
                    {"id": "other-model"},
                ],
            },
            "other": {"api": "openai-completions"},
        },
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    client = FakeClient({"vendor/model-1": [endpoint("a/fp8", "0.000001", "0.000002", "0.0000001")]})
    target = TargetConfig(
        name="local",
        kind="pi-model",
        pi_model_id="@preset/ds4-flash",
        pi_models_json=path,
        model="vendor/model-1",
    )
    backend = PiModelTarget(target, client)
    plan = backend.plan({"f": 0.9, "R": 0.01, "fw": 0.0}, RankConfig(top=1), "order")
    assert plan.changed is True

    backup = backend.apply(plan)
    assert backup is not None and backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == original

    updated = json.loads(path.read_text(encoding="utf-8"))
    entry = updated["providers"]["openrouter"]["models"][0]
    assert entry["contextWindow"] == 1000000
    assert entry["compat"]["keepMe"] is True
    assert entry["compat"]["openRouterRouting"]["order"] == ["a/fp8"]
    assert updated["providers"]["openrouter"]["models"][1] == {"id": "other-model"}
    assert updated["providers"]["other"] == {"api": "openai-completions"}
    assert updated["$schema"] == "https://pi.dev/schemas/models.schema.json"


def test_pi_model_target_noop_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "openrouter": {
                        "models": [
                            {
                                "id": "@preset/ds4-flash",
                                "compat": {"openRouterRouting": {"order": ["a/fp8"]}},
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = FakeClient({"vendor/model-1": [endpoint("a/fp8", "0.000001", "0.000002", "0.0000001")]})
    target = TargetConfig(
        name="local",
        kind="pi-model",
        pi_model_id="@preset/ds4-flash",
        pi_models_json=path,
        model="vendor/model-1",
    )
    backend = PiModelTarget(target, client)
    plan = backend.plan({"f": 0.9, "R": 0.01, "fw": 0.0}, RankConfig(), "order")
    assert plan.changed is False
    assert backend.apply(plan) is None
    assert list(tmp_path.glob("models.json.bak.*")) == []

    missing = TargetConfig(
        name="missing",
        kind="pi-model",
        pi_model_id="nope",
        pi_models_json=path,
        model="vendor/model-1",
    )
    with pytest.raises(ConfigError, match="not found"):
        PiModelTarget(missing, client)

    no_model = TargetConfig(
        name="no-model",
        kind="pi-model",
        pi_model_id="@preset/ds4-flash",
        pi_models_json=path,
    )
    with pytest.raises(ConfigError, match="requires an OpenRouter model"):
        PiModelTarget(no_model, client).plan({}, RankConfig(), "order")

    client.preset = preset_payload({"model": "vendor/model-1", "provider": {}})
    assert isinstance(
        open_target(TargetConfig(name="x", preset="p"), client),
        PresetTarget,
    )
    with pytest.raises(ConfigError, match="unknown kind"):
        open_target(TargetConfig(name="x", kind="weird"), client)
