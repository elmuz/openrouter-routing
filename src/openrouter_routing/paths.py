from __future__ import annotations

import os
from pathlib import Path

APP = "openrouter-routing"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    return Path(value).expanduser() if value else Path(fallback).expanduser()


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", "~/.config")


def cache_home() -> Path:
    return _xdg("XDG_CACHE_HOME", "~/.cache")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", "~/.local/state")


def config_dir() -> Path:
    return config_home() / APP


def cache_dir() -> Path:
    return cache_home() / APP


def state_dir() -> Path:
    return state_home() / APP


def default_config_path() -> Path:
    override = os.environ.get("OPENROUTER_ROUTING_CONFIG")
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def state_file() -> Path:
    return state_dir() / "state.json"


def lock_file() -> Path:
    return state_dir() / "refresh.lock"


def systemd_user_dir() -> Path:
    """XDG location for systemd *user* units (not app-specific)."""
    return config_home() / "systemd" / "user"


def pi_agent_dir() -> Path:
    override = os.environ.get("PI_AGENT_DIR")
    return Path(override).expanduser() if override else Path("~/.pi/agent").expanduser()


def default_sessions_glob() -> Path:
    return pi_agent_dir() / "sessions" / "**" / "*.jsonl"


def auth_path() -> Path:
    return pi_agent_dir() / "auth.json"


def models_json_path() -> Path:
    return pi_agent_dir() / "models.json"


def expand_path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
