from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openrouter_routing.config import (
    AppConfig,
    PathsConfig,
    RankConfig,
    RefreshConfig,
    TargetConfig,
)
from openrouter_routing.errors import ConfigError, OpenRouterError
from openrouter_routing.refresh import run_refresh, select_targets
from openrouter_routing.sessions import PiSessionSource

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


def endpoint(tag: str, prompt: str, completion: str, cr: str):
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
        "throughput_last_30m": {"p90": 100},
    }


class FakeClient:
    def __init__(self, config: dict, *, fail_preset: bool = False) -> None:
        self.config = config
        self.fail_preset = fail_preset
        self.posted: list[tuple[str, dict]] = []

    def get_preset(self, slug: str):
        if self.fail_preset:
            raise OpenRouterError("network down")
        return {
            "slug": slug,
            "designated_version": {"version": 1, "config": self.config},
        }

    def post_preset_config(self, slug: str, config: dict):
        self.posted.append((slug, config))
        self.config = config
        return {}

    def fetch_endpoints(self, model: str):
        return {
            "endpoints": [
                endpoint("a/fp8", "0.000001", "0.000002", "0.0000001"),
                endpoint("b/fp8", "0.000002", "0.000003", "0.0000002"),
            ]
        }

    def fetch_models_catalog(self):
        return []


def config_for(
    tmp_path: Path,
    *,
    targets: tuple[TargetConfig, ...],
    refresh: RefreshConfig | None = None,
) -> AppConfig:
    return AppConfig(
        paths=PathsConfig(tmp_path / "agent", FIXTURES / "**" / "*.jsonl"),
        rank=RankConfig(top=1),
        refresh=refresh or RefreshConfig(days=365, anchor="latest"),
        targets=targets,
    )


def source() -> PiSessionSource:
    return PiSessionSource(pattern=FIXTURES / "**" / "*.jsonl")


def target(**overrides) -> TargetConfig:
    base = {"name": "t", "kind": "preset", "preset": "p", "model": "vendor/model-1"}
    base.update(overrides)
    return TargetConfig(**base)


def test_refresh_dry_run_writes_nothing(tmp_path) -> None:
    client = FakeClient({"model": "vendor/model-1", "provider": {"order": ["old/fp8"]}})
    state = tmp_path / "state" / "state.json"
    lock = tmp_path / "state" / "refresh.lock"
    result = run_refresh(
        config_for(tmp_path, targets=(target(),)),
        source=source(),
        client=client,
        days=365,
        anchor="latest",
        apply=False,
        state_path=state,
        lock_path=lock,
    )
    assert result.applied is False
    assert result.plans[0].current_order == ("old/fp8",)
    assert result.plans[0].proposed_order == ("a/fp8",)
    assert client.posted == []
    assert not state.exists()
    assert not lock.exists()


def test_refresh_apply_records_state_and_order(tmp_path) -> None:
    client = FakeClient({"model": "vendor/model-1", "provider": {"order": ["old/fp8"]}})
    state = tmp_path / "state" / "state.json"
    lock = tmp_path / "state" / "refresh.lock"
    result = run_refresh(
        config_for(tmp_path, targets=(target(),)),
        source=source(),
        client=client,
        days=365,
        anchor="latest",
        apply=True,
        state_path=state,
        lock_path=lock,
    )
    assert result.applied is True
    assert result.applied_targets == {"t": True}
    assert client.posted == [("p", client.config)]
    assert client.config["provider"]["order"] == ["a/fp8"]

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["schema"] == "openrouter-routing/state/v1"
    entry = payload["run"]["targets"][0]
    assert entry["name"] == "t"
    assert entry["old_order"] == ["old/fp8"]
    assert entry["new_order"] == ["a/fp8"]
    assert entry["mix"]["requests"] == 6
    assert entry["applied"] is True
    assert not lock.exists()


def test_refresh_noop_apply_does_not_post(tmp_path) -> None:
    client = FakeClient({"model": "vendor/model-1", "provider": {"order": ["a/fp8"]}})
    state = tmp_path / "state" / "state.json"
    result = run_refresh(
        config_for(tmp_path, targets=(target(),)),
        source=source(),
        client=client,
        days=365,
        anchor="latest",
        apply=True,
        state_path=state,
        lock_path=tmp_path / "state" / "refresh.lock",
    )
    assert result.applied_targets == {"t": False}
    assert client.posted == []
    assert state.exists()


def test_refresh_target_mix_filters(tmp_path) -> None:
    client = FakeClient({"model": "vendor/model-1", "provider": {"order": ["old/fp8"]}})
    filtered = target(models=("@preset/main",))
    result = run_refresh(
        config_for(tmp_path, targets=(filtered,)),
        source=source(),
        client=client,
        days=365,
        anchor="latest",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )
    assert result.plans[0].mix["requests"] == 2


def test_refresh_network_failure_leaves_target_untouched(tmp_path) -> None:
    client = FakeClient(
        {"model": "vendor/model-1", "provider": {"order": ["old/fp8"]}},
        fail_preset=True,
    )
    state = tmp_path / "state" / "state.json"
    with pytest.raises(OpenRouterError, match="network down"):
        run_refresh(
            config_for(tmp_path, targets=(target(),)),
            source=source(),
            client=client,
            days=365,
            anchor="latest",
            apply=True,
            state_path=state,
            lock_path=tmp_path / "state" / "refresh.lock",
        )
    assert client.posted == []
    assert not state.exists()
    assert not (tmp_path / "state" / "refresh.lock").exists()


def test_refresh_lock_blocks_concurrent_apply(tmp_path) -> None:
    client = FakeClient({"model": "vendor/model-1", "provider": {"order": ["old/fp8"]}})
    lock = tmp_path / "refresh.lock"
    lock.write_text(f"{os.getpid()}\n2026-01-01T00:00:00+00:00\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="already running"):
        run_refresh(
            config_for(tmp_path, targets=(target(),)),
            source=source(),
            client=client,
            days=365,
            anchor="latest",
            apply=True,
            state_path=tmp_path / "state.json",
            lock_path=lock,
        )
    assert client.posted == []


def test_select_targets_by_name(tmp_path) -> None:
    cfg = config_for(
        tmp_path,
        targets=(
            TargetConfig(name="one", preset="p", model="vendor/model-1"),
            TargetConfig(name="two", preset="q", model="vendor/model-1"),
        ),
    )
    assert [item.name for item in select_targets(cfg)] == ["one", "two"]
    assert [item.name for item in select_targets(cfg, ["two", "two"])] == ["two"]
    with pytest.raises(ConfigError, match="unknown target"):
        select_targets(cfg, ["nope"])
    with pytest.raises(ConfigError, match="no refresh targets"):
        select_targets(
            AppConfig(paths=PathsConfig(tmp_path / "agent", FIXTURES / "**" / "*.jsonl")),
            ["x"],
        )
