from __future__ import annotations

import json
import os

import pytest

from openrouter_routing import paths, state
from openrouter_routing.errors import ConfigError


def test_state_round_trip_and_schema(tmp_path) -> None:
    path = tmp_path / "state.json"
    saved = state.save_state({"run": {"targets": []}}, path)
    assert saved == path
    payload = state.load_state(path)
    assert payload["schema"] == state.STATE_SCHEMA
    assert payload["updated_at"]
    assert payload["run"] == {"targets": []}
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_missing_and_invalid_state(tmp_path) -> None:
    assert state.load_state(tmp_path / "missing.json") == {"schema": state.STATE_SCHEMA}
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        state.load_state(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError):
        state.load_state(path)


def test_file_lock_blocks_overlapping_runs(tmp_path) -> None:
    path = tmp_path / "refresh.lock"
    with state.FileLock(path):
        assert path.exists()
        with pytest.raises(ConfigError, match="already running"):
            with state.FileLock(path, timeout=0):
                pass
    assert not path.exists()


def test_file_lock_removes_stale_pid(tmp_path) -> None:
    path = tmp_path / "refresh.lock"
    path.write_text("999999999\n2026-01-01T00:00:00+00:00\n", encoding="utf-8")
    with state.FileLock(path):
        assert path.exists()
    assert not path.exists()


def test_file_lock_keeps_lock_for_live_pid(tmp_path) -> None:
    path = tmp_path / "refresh.lock"
    path.write_text(f"{os.getpid()}\n2026-01-01T00:00:00+00:00\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        with state.FileLock(path, timeout=0):
            pass


def test_state_paths_follow_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert paths.state_file() == tmp_path / "state" / "openrouter-routing" / "state.json"
    assert paths.lock_file() == tmp_path / "state" / "openrouter-routing" / "refresh.lock"


def test_state_write_is_atomic_json(tmp_path) -> None:
    path = tmp_path / "state.json"
    state.save_state({"run": {"at": "x"}}, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run"]["at"] == "x"
