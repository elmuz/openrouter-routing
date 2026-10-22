from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths
from .errors import ConfigError
from .fsutil import atomic_write_json

STATE_SCHEMA = "openrouter-routing/state/v1"
LOCK_STALE_SECONDS = 3600.0


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def load_state(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else paths.state_file()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"schema": STATE_SCHEMA}
    except OSError as exc:
        raise ConfigError(f"cannot read state file {target}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"invalid JSON in state file {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"state file {target} must contain a JSON object")
    return payload


def save_state(payload: dict[str, Any], path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else paths.state_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema": STATE_SCHEMA, "updated_at": now_iso(), **payload}
    atomic_write_json(target, body, mode=0o600)
    return target


class FileLock:
    """Small XDG-state lock that prevents overlapping refresh runs.

    The lock records the owning PID. A lock whose process no longer exists is
    considered stale and removed, so a crashed timer cannot block refreshes
    forever.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        timeout: float = 0.0,
        poll: float = 0.1,
        stale_after: float = LOCK_STALE_SECONDS,
    ) -> None:
        self.path = Path(path) if path is not None else paths.lock_file()
        self.timeout = max(0.0, float(timeout))
        self.poll = max(0.01, float(poll))
        self.stale_after = max(0.0, float(stale_after))
        self._held = False

    def __enter__(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._remove_stale():
                    continue
                if time.monotonic() >= deadline:
                    owner = self._owner()
                    suffix = f" (owner {owner})" if owner else ""
                    raise ConfigError(
                        f"another refresh is already running{suffix}; "
                        f"remove {self.path} only if this is stale"
                    ) from None
                time.sleep(self.poll)
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(f"{os.getpid()}\n{now_iso()}\n")
                self._held = True
                return self

    def __exit__(self, *args: object) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def _owner(self) -> str:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        return lines[0].strip() if lines else ""

    def _remove_stale(self) -> bool:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return True
        except OSError:
            return False
        first = raw.splitlines()[0].strip() if raw.splitlines() else ""
        try:
            pid = int(first)
        except ValueError:
            pid = 0
        if pid > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return self._unlink()
            except PermissionError:
                return False
            else:
                return False
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True
        if age >= self.stale_after:
            return self._unlink()
        return False

    def _unlink(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True
