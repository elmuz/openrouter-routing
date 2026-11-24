from __future__ import annotations

import json
from pathlib import Path

from openrouter_routing.config import AppConfig, PathsConfig, TargetConfig
from openrouter_routing.doctor import Check, failed, format_check, run_doctor
from openrouter_routing.sessions import PiSessionSource

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


class FakeClient:
    def __init__(self, *, fail: bool = False, management: bool = True) -> None:
        self.fail = fail
        self.management = management

    def fetch_models_catalog(self):
        if self.fail:
            raise RuntimeError("offline")
        return [{"id": "a"}, {"id": "b"}]

    def fetch_key_info(self, *, write: bool = False):
        if self.fail:
            raise RuntimeError("offline")
        return {"is_management_key": self.management, "is_provisioning_key": self.management}


def make_models_json(tmp_path: Path) -> Path:
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
    return path


def config(tmp_path: Path, targets: tuple[TargetConfig, ...]) -> AppConfig:
    return AppConfig(
        paths=PathsConfig(tmp_path / "agent", FIXTURES / "**" / "*.jsonl"),
        targets=targets,
        source_path=tmp_path / "config.toml",
        loaded=False,
    )


def test_doctor_reports_healthy_local_target(tmp_path) -> None:
    target = TargetConfig(
        name="local",
        kind="pi-model",
        pi_model_id="@preset/ds4-flash",
        pi_models_json=make_models_json(tmp_path),
        model="vendor/model-1",
    )
    checks = run_doctor(
        config(tmp_path, (target,)),
        client=FakeClient(),
        source=PiSessionSource(pattern=FIXTURES / "**" / "*.jsonl"),
    )
    names = {check.name: check for check in checks}
    assert names["sessions"].ok and "6 usage record" in names["sessions"].detail
    assert names["network"].ok
    assert names["target local"].ok
    assert failed(checks) is False
    assert "ok" in format_check(names["network"])


def test_doctor_reports_network_and_target_failures(tmp_path) -> None:
    bad_preset = TargetConfig(name="bad", kind="preset", preset=None)
    no_model = TargetConfig(
        name="no-model",
        kind="pi-model",
        pi_model_id="@preset/ds4-flash",
        pi_models_json=make_models_json(tmp_path),
    )
    checks = run_doctor(
        config(tmp_path, (bad_preset, no_model)),
        client=FakeClient(fail=True),
        source=PiSessionSource(pattern=FIXTURES / "**" / "*.jsonl"),
    )
    assert failed(checks) is True
    assert any(check.name == "network" and not check.ok for check in checks)
    assert any(check.name == "target bad" and not check.ok for check in checks)
    assert any(check.name == "target no-model" and not check.ok for check in checks)


def test_doctor_warns_without_targets_or_sessions(tmp_path) -> None:
    empty = tmp_path / "sessions" / "**" / "*.jsonl"
    checks = run_doctor(
        config(tmp_path, ()),
        client=FakeClient(),
        source=PiSessionSource(pattern=empty),
    )
    names = {check.name: check for check in checks}
    assert names["targets"].warning is True
    assert names["sessions"].warning is True
    assert failed(checks) is False
    assert Check("x", True, "y").warning is False


def preset_target() -> TargetConfig:
    return TargetConfig(name="cloud", kind="preset", preset="ds4-flash", model="vendor/model")


def test_doctor_ok_with_a_management_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_KEY", "sk-management")
    checks = run_doctor(
        config(tmp_path, ()),
        client=FakeClient(),
        source=PiSessionSource(pattern=FIXTURES / "**" / "*.jsonl"),
    )
    write = next(check for check in checks if check.name == "auth write")
    assert write.ok and not write.warning
    assert "account scope" in write.detail


def test_doctor_warns_without_a_management_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENROUTER_MANAGEMENT_KEY", raising=False)
    checks = run_doctor(
        config(tmp_path, (preset_target(),)),
        client=FakeClient(),
        source=PiSessionSource(pattern=FIXTURES / "**" / "*.jsonl"),
    )
    write = next(check for check in checks if check.name == "auth write")
    assert write.ok and write.warning is True
    assert "401" in write.detail


def test_doctor_fails_when_the_write_key_is_not_a_management_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_KEY", "sk-inference")
    checks = run_doctor(
        config(tmp_path, (preset_target(),)),
        client=FakeClient(management=False),
        source=PiSessionSource(pattern=FIXTURES / "**" / "*.jsonl"),
    )
    write = next(check for check in checks if check.name == "auth write")
    assert write.ok is False
    assert failed(checks) is True
