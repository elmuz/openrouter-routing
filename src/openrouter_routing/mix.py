from __future__ import annotations

from collections.abc import Iterable, Sequence
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


def filter_records(
    records: Iterable[SessionRecord],
    *,
    models: Sequence[str] = (),
    routed_models: Sequence[str] = (),
    exclude_models: Sequence[str] = (),
    projects: Sequence[str] = (),
) -> list[SessionRecord]:
    rows: list[SessionRecord] = []
    for record in records:
        if not _matches(record.model, models):
            continue
        if not _matches(record.routed_model, routed_models):
            continue
        if exclude_models and _matches(record.model, exclude_models):
            continue
        if not _in_project(record.cwd, projects):
            continue
        rows.append(record)
    return rows


def _parse_required(value: str | None, what: str) -> datetime | None:
    if value is None:
        return None
    parsed = parse_ts(value)
    if parsed is None:
        raise ConfigError(f"invalid timestamp for {what}: {value!r}")
    return parsed


def time_filtered(
    records: Iterable[SessionRecord],
    *,
    hours: float | None = None,
    days: float | None = None,
    since: str | None = None,
    until: str | None = None,
    anchor: str = "latest",
) -> list[SessionRecord]:
    """Filter by a relative window or explicit bounds.

    `hours`/`days` keep the historical measure.py semantics by ending at the
    latest recorded event. Pass `anchor="now"` for wall-clock windows.
    """
    rows = list(records)
    if hours is not None and days is not None:
        raise ConfigError("use either --hours or --days, not both")
    if anchor not in {"latest", "now"}:
        raise ConfigError("anchor must be 'latest' or 'now'")

    since_ts = _parse_required(since, "--since")
    until_ts = _parse_required(until, "--until")
    if since_ts is None and (hours is not None or days is not None):
        span_hours = hours if hours is not None else float(days or 0) * 24
        if span_hours < 0:
            raise ConfigError("time window must be non-negative")
        base = latest_timestamp(rows)
        if anchor == "now" or base is None:
            base = datetime.now(UTC)
        since_ts = base - timedelta(hours=span_hours)

    if since_ts is not None:
        rows = [r for r in rows if r.timestamp is not None and r.timestamp >= since_ts]
    if until_ts is not None:
        rows = [r for r in rows if r.timestamp is not None and r.timestamp <= until_ts]
    return rows


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
