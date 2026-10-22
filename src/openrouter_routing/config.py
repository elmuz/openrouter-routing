from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .paths import (
    default_config_path,
    expand_path,
)
from .paths import (
    pi_agent_dir as default_pi_agent_dir,
)

DEFAULT_CONFIG_TEMPLATE = """\
# openrouter-routing configuration
#
# Paths default to XDG locations:
#   config: ${XDG_CONFIG_HOME:-~/.config}/openrouter-routing/config.toml
#   cache:  ${XDG_CACHE_HOME:-~/.cache}/openrouter-routing/
#   state:  ${XDG_STATE_HOME:-~/.local/state}/openrouter-routing/

[paths]
# pi_agent_dir = "~/.pi/agent"
# sessions_glob = "~/.pi/agent/sessions/**/*.jsonl"

[measure]
# Only apply a default window when no --hours/--days/--since is passed.
# days = 7
# projects = ["~/code", "~/projects"]
# models = ["@preset/ds4-flash"]
# routed_models = []
# exclude_models = []

[rank]
top = 4
healthy_only = true
require_cache = true
min_tps = 40
max_ttft = 6
tps_pct = "p90"
ttft_pct = "p90"
prefer = true
max_price_from = 0

# Routing targets. Add one block per model/preset you want to refresh.
# [[target]]
# name = "ds4-flash"
# kind = "preset"              # preset | pi-model (pi-model lands in Phase 4)
# preset = "ds4-flash"
# model = "~deepseek/deepseek-flash-latest"
# pi_model_id = "@preset/ds4-flash"
"""


@dataclass(frozen=True)
class PathsConfig:
    pi_agent_dir: Path
    sessions_glob: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "pi_agent_dir": str(self.pi_agent_dir),
            "sessions_glob": str(self.sessions_glob),
        }


@dataclass(frozen=True)
class MeasureConfig:
    days: float | None = None
    hours: float | None = None
    since: str | None = None
    until: str | None = None
    models: tuple[str, ...] = ()
    routed_models: tuple[str, ...] = ()
    exclude_models: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankConfig:
    top: int | None = None
    healthy_only: bool = False
    require_cache: bool = False
    min_tps: float | None = None
    max_ttft: float | None = None
    tps_pct: str = "p90"
    ttft_pct: str = "p90"
    prefer: bool = False
    max_price_from: int = 0
    quant: str | None = None


@dataclass(frozen=True)
class TargetConfig:
    name: str
    kind: str = "preset"
    preset: str | None = None
    model: str | None = None
    pi_models_json: Path | None = None
    pi_model_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "preset": self.preset,
            "model": self.model,
            "pi_models_json": str(self.pi_models_json) if self.pi_models_json else None,
            "pi_model_id": self.pi_model_id,
        }


@dataclass(frozen=True)
class AppConfig:
    paths: PathsConfig
    measure: MeasureConfig = field(default_factory=MeasureConfig)
    rank: RankConfig = field(default_factory=RankConfig)
    targets: tuple[TargetConfig, ...] = ()
    source_path: Path | None = None
    loaded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path) if self.source_path else None,
            "loaded": self.loaded,
            "paths": self.paths.to_dict(),
            "measure": {
                "days": self.measure.days,
                "hours": self.measure.hours,
                "since": self.measure.since,
                "until": self.measure.until,
                "models": list(self.measure.models),
                "routed_models": list(self.measure.routed_models),
                "exclude_models": list(self.measure.exclude_models),
                "projects": list(self.measure.projects),
            },
            "rank": {
                "top": self.rank.top,
                "healthy_only": self.rank.healthy_only,
                "require_cache": self.rank.require_cache,
                "min_tps": self.rank.min_tps,
                "max_ttft": self.rank.max_ttft,
                "tps_pct": self.rank.tps_pct,
                "ttft_pct": self.rank.ttft_pct,
                "prefer": self.rank.prefer,
                "max_price_from": self.rank.max_price_from,
                "quant": self.rank.quant,
            },
            "targets": [target.to_dict() for target in self.targets],
        }


def _as_float(value: Any, what: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{what} must be a number, got {value!r}") from exc


def _as_int(value: Any, what: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{what} must be an integer, got {value!r}") from exc


def _as_bool(value: Any, what: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ConfigError(f"{what} must be true or false, got {value!r}")
    return value


def _as_str_list(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{what} must be a list of strings")
    return tuple(value)


def _as_str(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{what} must be a string, got {value!r}")
    return value


def _paths_from(data: dict[str, Any], base: Path) -> PathsConfig:
    # A relative path in a config file is relative to that config file, not CWD.
    pi_raw = data.get("pi_agent_dir")
    pi_dir = expand_path(pi_raw) if isinstance(pi_raw, str) else default_pi_agent_dir()
    if not pi_dir.is_absolute():
        pi_dir = base / pi_dir
    pi_dir = pi_dir.resolve(strict=False)

    sessions_raw = data.get("sessions_glob")
    if isinstance(sessions_raw, str):
        sessions = expand_path(sessions_raw)
        if not sessions.is_absolute():
            sessions = base / sessions
        sessions = sessions.resolve(strict=False)
    else:
        sessions = pi_dir / "sessions" / "**" / "*.jsonl"
    return PathsConfig(pi_agent_dir=pi_dir, sessions_glob=sessions)


def _measure_from(data: dict[str, Any]) -> MeasureConfig:
    return MeasureConfig(
        days=_as_float(data.get("days"), "measure.days"),
        hours=_as_float(data.get("hours"), "measure.hours"),
        since=_as_str(data.get("since"), "measure.since"),
        until=_as_str(data.get("until"), "measure.until"),
        models=_as_str_list(data.get("models"), "measure.models"),
        routed_models=_as_str_list(data.get("routed_models"), "measure.routed_models"),
        exclude_models=_as_str_list(data.get("exclude_models"), "measure.exclude_models"),
        projects=_as_str_list(data.get("projects"), "measure.projects"),
    )


def _rank_from(data: dict[str, Any]) -> RankConfig:
    tps_pct = _as_str(data.get("tps_pct"), "rank.tps_pct") or "p90"
    ttft_pct = _as_str(data.get("ttft_pct"), "rank.ttft_pct") or "p90"
    for name, value in (("rank.tps_pct", tps_pct), ("rank.ttft_pct", ttft_pct)):
        if value not in {"p50", "p75", "p90", "p99"}:
            raise ConfigError(f"{name} must be one of p50, p75, p90, p99")
    top = _as_int(data.get("top"), "rank.top")
    if top is not None and top < 1:
        raise ConfigError("rank.top must be >= 1")
    return RankConfig(
        top=top,
        healthy_only=_as_bool(data.get("healthy_only"), "rank.healthy_only"),
        require_cache=_as_bool(data.get("require_cache"), "rank.require_cache"),
        min_tps=_as_float(data.get("min_tps"), "rank.min_tps"),
        max_ttft=_as_float(data.get("max_ttft"), "rank.max_ttft"),
        tps_pct=tps_pct,
        ttft_pct=ttft_pct,
        prefer=_as_bool(data.get("prefer"), "rank.prefer"),
        max_price_from=_as_int(data.get("max_price_from"), "rank.max_price_from") or 0,
        quant=_as_str(data.get("quant"), "rank.quant"),
    )


def _targets_from(data: Any, base: Path, pi_dir: Path) -> tuple[TargetConfig, ...]:
    if data is None:
        return ()
    rows = data if isinstance(data, list) else [data]
    targets: list[TargetConfig] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ConfigError(f"target[{index}] must be a table")
        name = _as_str(raw.get("name"), f"target[{index}].name") or f"target-{index}"
        kind = _as_str(raw.get("kind"), f"target[{index}].kind") or "preset"
        if kind not in {"preset", "pi-model"}:
            raise ConfigError(f"target[{index}].kind must be 'preset' or 'pi-model'")
        pi_json_raw = raw.get("pi_models_json")
        pi_json = None
        if isinstance(pi_json_raw, str):
            pi_json = expand_path(pi_json_raw)
            if not pi_json.is_absolute():
                pi_json = base / pi_json
            pi_json = pi_json.resolve(strict=False)
        elif kind == "pi-model":
            pi_json = pi_dir / "models.json"
        targets.append(
            TargetConfig(
                name=name,
                kind=kind,
                preset=_as_str(raw.get("preset"), f"target[{index}].preset"),
                model=_as_str(raw.get("model"), f"target[{index}].model"),
                pi_models_json=pi_json,
                pi_model_id=_as_str(raw.get("pi_model_id"), f"target[{index}].pi_model_id"),
            )
        )
    return tuple(targets)


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = expand_path(path) if path is not None else default_config_path()
    config_path = config_path.resolve(strict=False)
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError:
        return AppConfig(
            paths=PathsConfig(default_pi_agent_dir(), default_pi_agent_dir() / "sessions" / "**" / "*.jsonl"),
            source_path=config_path,
            loaded=False,
        )
    except OSError as exc:
        raise ConfigError(f"cannot read {config_path}: {exc}") from exc

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config root in {config_path} must be a table")

    base = config_path.parent
    paths = _paths_from(data.get("paths") or {}, base)
    measure = _measure_from(data.get("measure") or {})
    rank = _rank_from(data.get("rank") or {})
    targets = _targets_from(data.get("target"), base, paths.pi_agent_dir)
    return AppConfig(
        paths=paths, measure=measure, rank=rank, targets=targets, source_path=config_path, loaded=True
    )


def config_template() -> str:
    return DEFAULT_CONFIG_TEMPLATE
