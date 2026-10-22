from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openrouter_routing.errors import ConfigError
from openrouter_routing.mix import (
    accumulate,
    apply_window,
    filter_last_sessions,
    filter_records,
    group_by,
    resolve_window,
    time_filtered,
    validate_project_roots,
)
from openrouter_routing.sessions import load_records


@pytest.fixture
def records(session_paths: list[str]):
    return load_records(session_paths)


def test_accumulate(records) -> None:
    mix = accumulate(records)
    assert mix["requests"] == 6
    assert mix["input"] == 240
    assert mix["cacheRead"] == 1210
    assert mix["cacheWrite"] == 5
    assert mix["output"] == 29
    assert mix["total_input"] == 1450
    assert mix["f"] == pytest.approx(1210 / 1450)
    assert mix["R"] == pytest.approx(29 / 1450)
    assert mix["fw"] == pytest.approx(5 / 1450)


def test_accumulate_empty() -> None:
    assert accumulate([]) == {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "requests": 0,
        "f": 0.0,
        "R": 0.0,
        "fw": 0.0,
        "total_input": 0,
    }


def test_filter_by_project(records) -> None:
    rows = filter_records(records, projects=["/home/alice/projects/alpha"])
    assert len(rows) == 3
    assert {row.cwd for row in rows} == {"/home/alice/projects/alpha"}


def test_filter_by_model_and_routed_model(records) -> None:
    assert len(filter_records(records, models=["@preset/other"])) == 4
    assert len(filter_records(records, routed_models=["vendor/model-a"])) == 2
    assert len(filter_records(records, exclude_models=["@preset/other"])) == 2


def test_time_window_uses_latest_by_default(records) -> None:
    rows = time_filtered(records, hours=12)
    assert len(rows) == 3
    assert all(row.timestamp and row.timestamp.day == 2 for row in rows)


def test_time_window_anchor_now(records) -> None:
    # The fixture is in 2026; anchoring to wall clock yields no rows.
    assert time_filtered(records, hours=12, anchor="now") == []


def test_since_until_and_validation(records) -> None:
    rows = time_filtered(
        records,
        since="2026-01-01T10:00:02.000Z",
        until="2026-01-02T12:00:02.000Z",
    )
    assert [row.message_id for row in rows] == ["a2", "a3", "b1", "b2"]
    with pytest.raises(ConfigError):
        time_filtered(records, hours=1, days=1)
    with pytest.raises(ConfigError):
        time_filtered(records, since="nope")


def test_group_by(records) -> None:
    groups = group_by(records, "project")
    assert set(groups) == {"/home/alice/projects/alpha", "/home/alice/projects/beta"}
    assert len(group_by(records, "routed-model")["?"]) == 1


def test_filter_by_provider_and_exclude_project(records) -> None:
    assert len(filter_records(records, providers=["openrouter"])) == 6
    assert filter_records(records, providers=["nope"]) == []
    rows = filter_records(records, exclude_projects=["/home/alice/projects/alpha"])
    assert len(rows) == 3
    assert {row.cwd for row in rows} == {"/home/alice/projects/beta"}


def test_validate_project_roots_rejects_root() -> None:
    with pytest.raises(ConfigError, match="filesystem root"):
        validate_project_roots(["/"])
    with pytest.raises(ConfigError):
        validate_project_roots(["  "])
    validate_project_roots(["/home/alice/projects/alpha"])


def test_last_sessions_uses_most_recent(records) -> None:
    assert len(filter_last_sessions(records, None)) == 6
    assert len(filter_last_sessions(records, 2)) == 6
    recent = filter_last_sessions(records, 1)
    assert len(recent) == 3
    assert {row.session_id for row in recent} == {"beta-session"}
    with pytest.raises(ConfigError):
        filter_last_sessions(records, 0)


def test_resolve_window_anchor_and_explicit_bounds(records) -> None:
    latest = resolve_window(records, hours=12)
    assert latest.relative is True
    assert latest.since == datetime(2026, 1, 2, 0, 0, 3, tzinfo=UTC)
    assert latest.until is None

    now = resolve_window(records, hours=12, anchor="now")
    assert now.relative is True
    assert now.since > datetime(2026, 1, 2, 12, 0, 3, tzinfo=UTC)
    assert apply_window(records, now) == []

    explicit = resolve_window(records, since="2026-01-01T10:00:02.000Z", until="2026-01-01T10:00:03.000Z")
    assert explicit.relative is False
    assert [row.message_id for row in apply_window(records, explicit)] == ["a2", "a3"]

    # --since wins over a relative window.
    mixed = resolve_window(records, hours=1, since="2026-01-02T12:00:02.000Z")
    assert mixed.relative is False
    assert mixed.hours is None
    assert mixed.since == datetime(2026, 1, 2, 12, 0, 2, tzinfo=UTC)

    with pytest.raises(ConfigError):
        resolve_window(records, hours=1, anchor="yesterday")
