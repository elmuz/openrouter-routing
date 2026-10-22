from __future__ import annotations

from openrouter_routing import paths


def test_xdg_overrides(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert paths.config_dir() == tmp_path / "cfg" / "openrouter-routing"
    assert paths.cache_dir() == tmp_path / "cache" / "openrouter-routing"
    assert paths.state_dir() == tmp_path / "state" / "openrouter-routing"


def test_config_env_override(monkeypatch, tmp_path) -> None:
    target = tmp_path / "custom.toml"
    monkeypatch.setenv("OPENROUTER_ROUTING_CONFIG", str(target))
    assert paths.default_config_path() == target


def test_pi_agent_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PI_AGENT_DIR", str(tmp_path / "agent"))
    assert paths.pi_agent_dir() == tmp_path / "agent"
    assert paths.auth_path() == tmp_path / "agent" / "auth.json"
    assert paths.default_sessions_glob() == tmp_path / "agent" / "sessions" / "**" / "*.jsonl"
