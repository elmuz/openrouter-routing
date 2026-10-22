from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openrouter_routing import fsutil


def test_atomic_write_replaces_and_keeps_no_temp_files(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text("old\n", encoding="utf-8")
    fsutil.atomic_write_text(path, "new\n", mode=0o600)
    assert path.read_text(encoding="utf-8") == "new\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert [item.name for item in tmp_path.iterdir()] == ["models.json"]


def test_atomic_write_failure_leaves_original_intact(tmp_path, monkeypatch) -> None:
    path = tmp_path / "models.json"
    path.write_text("old\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        fsutil.atomic_write_text(path, "new\n")
    assert path.read_text(encoding="utf-8") == "old\n"
    assert [item.name for item in tmp_path.iterdir()] == ["models.json"]


def test_atomic_write_json_round_trips(tmp_path) -> None:
    path = tmp_path / "state.json"
    fsutil.atomic_write_json(path, {"a": [1, 2], "b": "x"})
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"a"' in path.read_text(encoding="utf-8")


def test_backup_file_is_timestamped_and_collision_safe(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text("one\n", encoding="utf-8")
    now = datetime(2026, 10, 22, 23, 0, tzinfo=UTC)
    first = fsutil.backup_file(path, now=now)
    second = fsutil.backup_file(path, now=now)
    assert first is not None and second is not None
    assert first.name == "models.json.bak.20261022T230000Z"
    assert second.name == "models.json.bak.20261022T230000Z.2"
    assert first.read_text(encoding="utf-8") == "one\n"
    assert fsutil.backup_file(tmp_path / "missing.json") is None
