from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .sessions import SessionRecord, latest_timestamp, parse_ts


def accumulate(records: Iterable[SessionRecord]) -> dict[str, Any]:
    total = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "requests": 0}
    for record in records:
        total["requests"] += 1
        total["input"] += record.input_tokens
        total["output"] += record.output_tokens
        total["cacheRead"] += record.cache_read
        total["cacheWrite"] += record.cache_write
    total_input = total["input"] + total["cacheRead"]
    if not total_input:
        return {**total, "f": 0.0, "R": 0.0, "fw": 0.0, "total_input": 0}
    return {
        **total,
        "total_input": total_input,
        "f": total["cacheRead"] / total_input,
        "R": total["output"] / total_input,
        "fw": total["cacheWrite"] / total_input,
    }


def _matches(value: str | None, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    if not value:
        return False
    lowered = value.lower()
    return any(
        lowered == pattern.lower() or pattern.lower() in lowered or fnmatchcase(lowered, pattern.lower())
        for pattern in patterns
    )


def _in_project(cwd: str | None, roots: Sequence[str]) -> bool:
    if not roots:
        return True
    if not cwd:
        return False
    candidate = Path(cwd).expanduser().resolve(strict=False)
    for root in roots:
        base = Path(root).expanduser().resolve(strict=False)
        if candidate == base or base in candidate.parents:
            return True
    return False


def validate_project_roots(roots: Sequence[str]) -> None:
    """Reject filters that would silently match every record on the machine."""
    for root in roots:
        text = str(root).strip()
        if not text:
            raise ConfigError("project filter must not be empty")
        resolved = Path(text).expanduser().resolve(strict=False)
        if resolved.parent == resolved:
            raise ConfigError(f"refusing to use the filesystem root as a project filter: {root!r}")


def filter_records(
    records: Iterable[SessionRecord],
    *,
    models: Sequence[str] = (),
    routed_models: Sequence[str] = (),
    exclude_models: Sequence[str] = (),
    providers: Sequence[str] = (),
    projects: Sequence[str] = (),
    exclude_projects: Sequence[str] = (),
) -> list[SessionRecord]:
    rows: list[SessionRecord] = []
    for record in records:
        if not _matches(record.model, models):
            continue
        if not _matches(record.routed_model, routed_models):
            continue
        if exclude_models and _matches(record.model, exclude_models):
            continue
        if not _matches(record.provider, providers):
            continue
        if not _in_project(record.cwd, projects):
            continue
        if exclude_projects and _in_project(record.cwd, exclude_projects):
            continue
        rows.append(record)
    return rows


def _session_key(record: SessionRecord) -> str:
    return record.session_id or record.path


def filter_last_sessions(records: Iterable[SessionRecord], count: int | None) -> list[SessionRecord]:
    """Keep the `count` most recent sessions (all records from each session).

    Sessions are ordered by their latest record timestamp. Session identity is
    `session_id` when present, otherwise the file path. This is applied before
    the time window so `--last-sessions 3 --days 2` means "the last three
    sessions, then the last two days of those sessions".
    """
    rows = list(records)
    if count is None:
        return rows
    if count < 1:
        raise ConfigError("--last-sessions must be >= 1")
    oldest = datetime.min.replace(tzinfo=UTC)
    latest: dict[str, datetime] = {}
    for record in rows:
        key = _session_key(record)
        stamp = record.timestamp or oldest
        if key not in latest or stamp > latest[key]:
            latest[key] = stamp
    keep = set(sorted(latest, key=lambda key: (latest[key], key))[-count:])
    return [record for record in rows if _session_key(record) in keep]


def _parse_required(value: str | None, what: str) -> datetime | None:
    if value is None:
        return None
    parsed = parse_ts(value)
    if parsed is None:
        raise ConfigError(f"invalid timestamp for {what}: {value!r}")
    return parsed


@dataclass(frozen=True)
class TimeWindow:
    """Resolved time bounds; both `since` and `until` are inclusive."""

    since: datetime | None = None
    until: datetime | None = None
    anchor: str = "latest"
    hours: float | None = None
    days: float | None = None
    relative: bool = False


def resolve_window(
    records: Iterable[SessionRecord],
    *,
    hours: float | None = None,
    days: float | None = None,
    since: str | None = None,
    until: str | None = None,
    anchor: str = "latest",
) -> TimeWindow:
    """Resolve relative/explicit time bounds without filtering records yet.

    `--since` and `--until` are both inclusive. `--hours`/`--days` end at the
    latest recorded event by default; `anchor="now"` uses wall-clock time
    instead. An explicit `--since` wins over a relative window.
    """
    rows = list(records)
    if hours is not None and days is not None:
        raise ConfigError("use either --hours or --days, not both")
    if anchor not in {"latest", "now"}:
        raise ConfigError("anchor must be 'latest' or 'now'")

    since_ts = _parse_required(since, "--since")
    until_ts = _parse_required(until, "--until")
    relative = False
    if since_ts is None and (hours is not None or days is not None):
        span_hours = hours if hours is not None else float(days or 0) * 24
        if span_hours < 0:
            raise ConfigError("time window must be non-negative")
        base = latest_timestamp(rows)
        if anchor == "now" or base is None:
            base = datetime.now(UTC)
        since_ts = base - timedelta(hours=span_hours)
        relative = True

    return TimeWindow(
        since=since_ts,
        until=until_ts,
        anchor=anchor,
        hours=hours if relative else None,
        days=days if relative else None,
        relative=relative,
    )


def apply_window(records: Iterable[SessionRecord], window: TimeWindow) -> list[SessionRecord]:
    rows = list(records)
    if window.since is not None:
        rows = [row for row in rows if row.timestamp is not None and row.timestamp >= window.since]
    if window.until is not None:
        rows = [row for row in rows if row.timestamp is not None and row.timestamp <= window.until]
    return rows


def time_filtered(
    records: Iterable[SessionRecord],
    *,
    hours: float | None = None,
    days: float | None = None,
    since: str | None = None,
    until: str | None = None,
    anchor: str = "latest",
) -> list[SessionRecord]:
    """Filter by a relative window or explicit inclusive bounds.

    `hours`/`days` keep the historical measure.py semantics by ending at the
    latest recorded event. Pass `anchor="now"` for wall-clock windows.
    """
    rows = list(records)
    window = resolve_window(rows, hours=hours, days=days, since=since, until=until, anchor=anchor)
    return apply_window(rows, window)


def group_by(records: Iterable[SessionRecord], key: str) -> dict[str, list[SessionRecord]]:
    groups: dict[str, list[SessionRecord]] = {}
    for record in records:
        if key == "provider-model":
            name = f"{record.provider}/{record.model}"
        elif key == "model":
            name = record.model
        elif key == "routed-model":
            name = record.routed_model or "?"
        elif key == "project":
            name = record.cwd or "?"
        else:
            raise ConfigError(f"unknown group key: {key}")
        groups.setdefault(name, []).append(record)
    return groups


def percent(value: float) -> str:
    return f"{100 * value:.2f}%"
