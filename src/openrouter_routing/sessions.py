from __future__ import annotations

import glob
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import default_sessions_glob


@dataclass(frozen=True)
class SessionRecord:
    """One billed assistant request from a pi session log."""

    timestamp: datetime | None
    provider: str
    model: str
    routed_model: str | None
    usage: dict[str, Any]
    cwd: str | None
    session_id: str | None
    path: str
    message_id: str | None

    @property
    def input_tokens(self) -> int:
        return int(self.usage.get("input") or 0)

    @property
    def output_tokens(self) -> int:
        return int(self.usage.get("output") or 0)

    @property
    def cache_read(self) -> int:
        return int(self.usage.get("cacheRead") or 0)

    @property
    def cache_write(self) -> int:
        return int(self.usage.get("cacheWrite") or 0)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def discover(
    paths: Iterable[str | os.PathLike[str]] | None = None, pattern: str | os.PathLike[str] | None = None
) -> list[str]:
    if paths:
        return sorted(str(Path(path).expanduser()) for path in paths)
    glob_pattern = str(Path(pattern).expanduser()) if pattern else str(default_sessions_glob())
    return sorted(glob.glob(glob_pattern, recursive=True))


def _record_from(
    rec: dict[str, Any],
    *,
    cwd: str | None,
    session_id: str | None,
    path: str,
) -> SessionRecord | None:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict) or "cacheRead" not in usage:
        return None
    return SessionRecord(
        timestamp=parse_ts(rec.get("timestamp") or msg.get("timestamp")),
        provider=msg.get("provider") or "?",
        model=msg.get("model") or "?",
        routed_model=msg.get("responseModel"),
        usage=usage,
        cwd=cwd,
        session_id=session_id,
        path=path,
        message_id=rec.get("id"),
    )


def iter_records(paths: Iterable[str | os.PathLike[str]]) -> Iterator[SessionRecord]:
    """Yield deduplicated assistant usage records from pi session JSONL files."""
    seen: set[str] = set()
    for path in paths:
        cwd: str | None = None
        session_id: str | None = None
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"usage"' not in line and '"type"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") == "session":
                    cwd = rec.get("cwd") or cwd
                    session_id = rec.get("id") or session_id
                    continue
                record = _record_from(rec, cwd=cwd, session_id=session_id, path=str(path))
                if record is None:
                    continue
                if record.message_id:
                    if record.message_id in seen:
                        continue
                    seen.add(record.message_id)
                yield record


def load_records(
    paths: Iterable[str | os.PathLike[str]] | None = None,
    *,
    pattern: str | os.PathLike[str] | None = None,
) -> list[SessionRecord]:
    return list(iter_records(discover(paths, pattern)))


def latest_timestamp(records: Iterable[SessionRecord]) -> datetime | None:
    latest: datetime | None = None
    for record in records:
        if record.timestamp and (latest is None or record.timestamp > latest):
            latest = record.timestamp
    return latest
