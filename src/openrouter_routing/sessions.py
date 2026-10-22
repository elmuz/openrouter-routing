from __future__ import annotations

import glob
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .errors import ConfigError
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
    response_id: str | None = None

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


@runtime_checkable
class SessionSource(Protocol):
    """Minimal contract for a harness-specific session log adapter."""

    name: str

    def discover(self) -> list[str]:
        """Return the session files this source would read."""
        ...

    def iter_records(self, paths: Iterable[str | os.PathLike[str]] | None = None) -> Iterator[SessionRecord]:
        """Yield normalized records, optionally from explicit paths."""
        ...


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # pi writes UTC 'Z' stamps, but keep old/hand-written logs comparable too.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def infer_cwd_from_path(path: str | os.PathLike[str]) -> str | None:
    """Best-effort fallback cwd from a pi session directory name.

    pi names session directories after the working directory, e.g.
    ``--home-alice-projects-alpha--`` for ``/home/alice/projects/alpha``.
    The encoding is lossy for directory names containing hyphens, so this is
    only used when the session header does not carry a cwd.
    """
    parent = Path(path).parent.name
    if len(parent) > 4 and parent.startswith("--") and parent.endswith("--"):
        inner = parent[2:-2]
        if inner:
            return "/" + inner.replace("-", "/")
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
    if not isinstance(usage, dict):
        # A few older/hand-written logs keep usage next to the message.
        usage = rec.get("usage")
    if not isinstance(usage, dict) or "cacheRead" not in usage:
        return None
    return SessionRecord(
        timestamp=parse_ts(_text(rec.get("timestamp")) or _text(msg.get("timestamp"))),
        provider=_text(msg.get("provider")) or _text(rec.get("provider")) or "?",
        model=_text(msg.get("model")) or _text(rec.get("model")) or "?",
        routed_model=(
            _text(msg.get("responseModel"))
            or _text(msg.get("response_model"))
            or _text(rec.get("responseModel"))
        ),
        usage=usage,
        cwd=cwd,
        session_id=session_id,
        path=path,
        message_id=_text(rec.get("id")) or _text(msg.get("id")),
        response_id=(
            _text(msg.get("responseId"))
            or _text(msg.get("response_id"))
            or _text(msg.get("generationId"))
            or _text(msg.get("generation_id"))
            or _text(rec.get("responseId"))
            or _text(rec.get("response_id"))
        ),
    )


def _iter_records(paths: Iterable[str | os.PathLike[str]]) -> Iterator[SessionRecord]:
    """Yield deduplicated assistant usage records from pi session JSONL files."""
    seen: set[str] = set()
    for raw_path in paths:
        path = str(raw_path)
        # Fallbacks for truncated files whose session header never made it.
        # Keep session_id unset so `--last-sessions` falls back to the file path
        # instead of merging unrelated logs that share a file name.
        cwd = infer_cwd_from_path(path)
        session_id: str | None = None
        try:
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ConfigError(f"cannot read session file {path}: {exc}") from exc
        with handle:
            for line in handle:
                if '"usage"' not in line and '"type"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (TypeError, ValueError):
                    # Partially written last line, stray text, etc.
                    continue
                if not isinstance(rec, dict):
                    continue
                if str(rec.get("type") or "").strip().lower() == "session":
                    header_cwd = _text(rec.get("cwd"))
                    if header_cwd:
                        cwd = header_cwd
                    session_id = _text(rec.get("id")) or _text(rec.get("sessionId")) or session_id
                    continue
                record = _record_from(rec, cwd=cwd, session_id=session_id, path=path)
                if record is None:
                    continue
                if record.message_id:
                    if record.message_id in seen:
                        continue
                    seen.add(record.message_id)
                yield record


def iter_records(paths: Iterable[str | os.PathLike[str]]) -> Iterator[SessionRecord]:
    return _iter_records(paths)


@dataclass(frozen=True)
class PiSessionSource:
    """pi JSONL adapter; the only implementation of `SessionSource` today."""

    pattern: str | os.PathLike[str] | None = None
    paths: tuple[str, ...] = ()
    name: str = "pi"

    def discover(self) -> list[str]:
        return discover(self.paths or None, pattern=self.pattern)

    def iter_records(self, paths: Iterable[str | os.PathLike[str]] | None = None) -> Iterator[SessionRecord]:
        if paths is None:
            paths = self.paths or self.discover()
        return _iter_records(paths)


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
