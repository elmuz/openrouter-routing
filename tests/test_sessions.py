from __future__ import annotations

from datetime import UTC, datetime

from openrouter_routing.sessions import discover, latest_timestamp, load_records, parse_ts


def test_parse_ts_accepts_z() -> None:
    assert parse_ts("2026-01-01T10:00:00.000Z") == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert parse_ts(None) is None
    assert parse_ts("not a timestamp") is None


def test_discover_and_load_deduplicates(session_paths: list[str]) -> None:
    records = load_records(session_paths)
    assert len(records) == 6
    assert all(record.usage.get("cacheRead") is not None for record in records)
    assert {record.message_id for record in records} == {"a1", "a2", "a3", "b1", "b2", "b3"}


def test_session_cwd_and_routed_model(session_paths: list[str]) -> None:
    records = load_records(session_paths)
    alpha = [record for record in records if record.cwd == "/home/alice/projects/alpha"]
    assert len(alpha) == 3
    assert alpha[0].session_id == "alpha-session"
    assert alpha[0].routed_model == "vendor/model-a"


def test_latest_timestamp(session_paths: list[str]) -> None:
    records = load_records(session_paths)
    assert latest_timestamp(records) == datetime(2026, 1, 2, 12, 0, 3, tzinfo=UTC)


def test_discover_uses_recursive_glob(tmp_path) -> None:
    nested = tmp_path / "sessions" / "project" / "session.jsonl"
    nested.parent.mkdir(parents=True)
    nested.write_text(
        '{"type":"session","id":"s","cwd":"/tmp/p"}\n'
        '{"type":"message","id":"m","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":1,"output":1,"cacheWrite":0}}}\n',
        encoding="utf-8",
    )
    assert discover(pattern=tmp_path / "sessions" / "**" / "*.jsonl") == [str(nested)]
