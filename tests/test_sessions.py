from __future__ import annotations

from datetime import UTC, datetime

from openrouter_routing.sessions import (
    PiSessionSource,
    SessionSource,
    discover,
    infer_cwd_from_path,
    latest_timestamp,
    load_records,
    parse_ts,
)


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


def test_infer_cwd_from_session_directory() -> None:
    assert infer_cwd_from_path("/x/--home-alice-projects-alpha--/s.jsonl") == "/home/alice/projects/alpha"
    assert infer_cwd_from_path("/x/plain/s.jsonl") is None


def test_missing_header_and_whitespace_variant(tmp_path) -> None:
    fallback = tmp_path / "--home-alice-projects-gamma--" / "s.jsonl"
    fallback.parent.mkdir()
    fallback.write_text(
        '{"type":"message","id":"m1","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":2,"output":1,"cacheWrite":0}}}\n',
        encoding="utf-8",
    )
    (record,) = load_records([fallback])
    assert record.cwd == "/home/alice/projects/gamma"
    assert record.session_id is None

    header = tmp_path / "variant.jsonl"
    header.write_text(
        '{"type":" Session ","id":"s1","cwd":"/tmp/variant"}\n'
        '{"type":"message","id":"m2","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":2,"output":1,"cacheWrite":0}}}\n',
        encoding="utf-8",
    )
    (record,) = load_records([header])
    assert record.cwd == "/tmp/variant"
    assert record.session_id == "s1"


def test_malformed_and_truncated_lines_are_skipped(tmp_path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text(
        '{"type":"session","id":"s","cwd":"/tmp/broken"}\n'
        '{"type":"message","id":"ok1","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":2,"output":1,"cacheWrite":0}}}\n'
        '{"type":"message","id":"truncated","message":{"provider"\n'
        "[]\n"
        "not json at all\n"
        '{"type":"message","id":"nomessage"}\n'
        '{"type":"message","id":"nousage","message":{"provider":"openrouter","model":"m"}}\n'
        '{"type":"message","id":"nocache","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"output":1}}}\n'
        '{"type":"message","id":"ok1","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":2,"output":1,"cacheWrite":0}}}\n',
        encoding="utf-8",
    )
    records = load_records([path])
    assert [record.message_id for record in records] == ["ok1"]


def test_duplicate_ids_across_files_are_deduped(tmp_path) -> None:
    line = (
        '{"type":"session","id":"s","cwd":"/tmp/dup"}\n'
        '{"type":"message","id":"same","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":2,"output":1,"cacheWrite":0}}}\n'
    )
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(line, encoding="utf-8")
    second.write_text(line, encoding="utf-8")
    assert len(load_records([first, second])) == 1


def test_pi_session_source_implements_protocol(tmp_path) -> None:
    path = tmp_path / "--home-alice-projects-delta--" / "s.jsonl"
    path.parent.mkdir()
    path.write_text(
        '{"type":"message","id":"m","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":2,"output":1,"cacheWrite":0}}}\n',
        encoding="utf-8",
    )
    source: SessionSource = PiSessionSource(pattern=tmp_path / "**" / "*.jsonl")
    assert isinstance(source, SessionSource)
    assert source.discover() == [str(path)]
    assert [record.cwd for record in source.iter_records()] == ["/home/alice/projects/delta"]


def test_naive_timestamp_is_treated_as_utc() -> None:
    assert parse_ts("2026-01-01T10:00:00") == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def test_unreadable_session_file_is_a_config_error(tmp_path) -> None:
    import pytest

    from openrouter_routing.errors import ConfigError

    with pytest.raises(ConfigError, match="cannot read session file"):
        load_records([tmp_path / "missing.jsonl"])


def test_response_id_variants(tmp_path) -> None:
    path = tmp_path / "response-ids.jsonl"
    path.write_text(
        '{"type":"session","id":"s","cwd":"/tmp/ids"}\n'
        '{"type":"message","id":"m1","message":{"provider":"openrouter","model":"m",'
        '"responseId":"gen-1","usage":{"input":1,"cacheRead":1,"output":1,"cacheWrite":0}}}\n'
        '{"type":"message","id":"m2","message":{"provider":"openrouter","model":"m",'
        '"response_id":"gen-2","usage":{"input":1,"cacheRead":1,"output":1,"cacheWrite":0}}}\n'
        '{"type":"message","id":"m3","responseId":"gen-3","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":1,"output":1,"cacheWrite":0}}}\n',
        encoding="utf-8",
    )
    records = load_records([path])
    assert [record.response_id for record in records] == ["gen-1", "gen-2", "gen-3"]
