from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ConfigError


def file_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Write `text` to `path` through a same-directory temporary file.

    A failed write never truncates the existing file. The caller is responsible
    for validating the content before calling this function.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: str | Path, payload: Any, *, mode: int | None = None) -> None:
    """Serialize, validate, then atomically replace a JSON file."""
    text = json.dumps(payload, indent=2) + "\n"
    try:
        json.loads(text)
    except ValueError as exc:  # pragma: no cover - a bug in the caller
        raise ConfigError(f"refusing to write invalid JSON to {path}: {exc}") from exc
    atomic_write_text(path, text, mode=mode)


def backup_file(path: str | Path, *, now: datetime | None = None) -> Path | None:
    """Copy `path` to a timestamped sibling before a destructive update."""
    source = Path(path)
    if not source.exists():
        return None
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    candidate = source.with_name(f"{source.name}.bak.{stamp}")
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = source.with_name(f"{source.name}.bak.{stamp}.{counter}")
    shutil.copy2(source, candidate)
    return candidate
