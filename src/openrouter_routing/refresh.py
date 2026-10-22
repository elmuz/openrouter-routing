from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .api import OpenRouterClient
from .config import AppConfig, TargetConfig
from .errors import ConfigError
from .mix import (
    TimeWindow,
    accumulate,
    apply_window,
    filter_records,
    resolve_window,
    validate_project_roots,
)
from .sessions import SessionSource
from .state import FileLock, now_iso, save_state
from .targets import PiModelTarget, PresetTarget, TargetPlan, open_target

_MIX_KEYS = (
    "requests",
    "input",
    "output",
    "cacheRead",
    "cacheWrite",
    "total_input",
    "f",
    "R",
    "fw",
)


@dataclass(frozen=True)
class RefreshResult:
    window: TimeWindow
    plans: tuple[TargetPlan, ...]
    applied: bool
    applied_targets: dict[str, bool]
    backups: dict[str, Path | None]
    state_path: Path | None
    window_since: str | None
    window_until: str | None


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


def select_targets(cfg: AppConfig, names: Sequence[str] = ()) -> tuple[TargetConfig, ...]:
    if not cfg.targets:
        raise ConfigError("no refresh targets configured; add a [[target]] block to config.toml")
    if not names:
        return cfg.targets
    by_name = {target.name: target for target in cfg.targets}
    selected: list[TargetConfig] = []
    for name in names:
        target = by_name.get(name)
        if target is None:
            available = ", ".join(sorted(by_name)) or "none"
            raise ConfigError(f"unknown target {name!r} (available: {available})")
        if target not in selected:
            selected.append(target)
    return tuple(selected)


def _window_choice(
    cfg: AppConfig,
    *,
    hours: float | None,
    days: float | None,
    since: str | None,
    until: str | None,
    anchor: str | None,
) -> dict[str, Any]:
    if hours is not None:
        use_hours, use_days = hours, None
    elif days is not None:
        use_hours, use_days = None, days
    else:
        use_hours, use_days = cfg.refresh.hours, cfg.refresh.days
    return {
        "hours": use_hours,
        "days": use_days,
        "since": since if since is not None else cfg.refresh.since,
        "until": until if until is not None else cfg.refresh.until,
        "anchor": anchor or cfg.refresh.anchor,
    }


def _target_rows(rows: list[Any], target: TargetConfig) -> list[Any]:
    roots = [*target.projects, *target.exclude_projects]
    if roots:
        validate_project_roots(roots)
    return filter_records(
        rows,
        models=target.models,
        routed_models=target.routed_models,
        exclude_models=target.exclude_models,
        projects=target.projects,
        exclude_projects=target.exclude_projects,
    )


def _mix_payload(mix: dict[str, Any]) -> dict[str, Any]:
    return {key: mix[key] for key in _MIX_KEYS}


def _state_payload(
    window: TimeWindow,
    plans: Sequence[TargetPlan],
    *,
    applied: bool,
    applied_targets: dict[str, bool],
    backups: dict[str, Path | None],
) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    for plan in plans:
        name = plan.target.name
        targets.append(
            {
                "name": name,
                "kind": plan.target.kind,
                "model": plan.model,
                "resolved_model": plan.resolved_model,
                "routing_mode": plan.mode,
                "mix": _mix_payload(plan.mix),
                "old_order": list(plan.current_order),
                "new_order": list(plan.proposed_order),
                "old_routing": plan.current_routing,
                "new_routing": plan.proposed_routing,
                "changed": plan.changed,
                "applied": applied_targets.get(name, False),
                "backup": str(backups[name]) if backups.get(name) else None,
            }
        )
    return {
        "run": {
            "at": now_iso(),
            "applied": applied,
            "window": {
                "since": window.since.isoformat() if window.since else None,
                "until": window.until.isoformat() if window.until else None,
                "anchor": window.anchor,
                "hours": window.hours,
                "days": window.days,
                "relative": window.relative,
            },
            "targets": targets,
        }
    }


def run_refresh(
    cfg: AppConfig,
    *,
    source: SessionSource,
    client: OpenRouterClient,
    target_names: Sequence[str] = (),
    hours: float | None = None,
    days: float | None = None,
    since: str | None = None,
    until: str | None = None,
    anchor: str | None = None,
    apply: bool = False,
    on_plan: Callable[[Sequence[TargetPlan]], None] | None = None,
    log: Callable[[str], None] | None = None,
    state_path: Path | None = None,
    lock_path: Path | None = None,
) -> RefreshResult:
    targets = select_targets(cfg, target_names)
    _log(log, f"refresh start: {len(targets)} target(s), apply={apply}")

    with FileLock(lock_path or paths.lock_file(), timeout=cfg.refresh.lock_timeout):
        session_paths = source.discover()
        if not session_paths:
            raise ConfigError(f"no session files found under {cfg.paths.sessions_glob}")
        records = list(source.iter_records(session_paths))
        if not records:
            raise ConfigError("no usage records found in the selected session files")

        choice = _window_choice(cfg, hours=hours, days=days, since=since, until=until, anchor=anchor)
        window = resolve_window(
            records,
            hours=choice["hours"],
            days=choice["days"],
            since=choice["since"],
            until=choice["until"],
            anchor=choice["anchor"],
        )
        rows = apply_window(records, window)
        if not rows:
            raise ConfigError("no usage records in the selected refresh window")
        _log(
            log,
            f"window: since={window.since} until={window.until} anchor={window.anchor}",
        )

        backends: list[PresetTarget | PiModelTarget] = []
        plans: list[TargetPlan] = []
        for target in targets:
            selected = _target_rows(rows, target)
            if not selected:
                raise ConfigError(
                    f"target {target.name!r}: no usage records in the window after its mix filters"
                )
            mix = accumulate(selected)
            mode = target.routing_mode or cfg.refresh.routing_mode
            backend = open_target(target, client)
            plan = backend.plan(mix, cfg.rank, mode)
            backends.append(backend)
            plans.append(plan)
            _log(
                log,
                f"target {target.name}: {plan.resolved_model} mode={plan.mode} changed={plan.changed}",
            )

        if on_plan is not None:
            on_plan(plans)

        applied_targets = {plan.target.name: False for plan in plans}
        backups: dict[str, Path | None] = {}
        saved: Path | None = None
        if apply:
            for backend, plan in zip(backends, plans, strict=True):
                if not plan.changed:
                    _log(log, f"target {plan.target.name}: unchanged, no write")
                    continue
                if isinstance(backend, PresetTarget) and plan.system_prompt:
                    _log(
                        log,
                        f"target {plan.target.name}: warning: preset has a system_prompt",
                    )
                backup = backend.apply(plan)
                backups[plan.target.name] = backup
                applied_targets[plan.target.name] = True
                detail = f", backup={backup}" if backup else ""
                _log(log, f"target {plan.target.name}: applied{detail}")
            payload = _state_payload(
                window,
                plans,
                applied=True,
                applied_targets=applied_targets,
                backups=backups,
            )
            saved = save_state(payload, state_path)
            _log(log, f"state: {saved}")

    return RefreshResult(
        window=window,
        plans=tuple(plans),
        applied=apply,
        applied_targets=applied_targets,
        backups=backups,
        state_path=saved,
        window_since=window.since.isoformat() if window.since else None,
        window_until=window.until.isoformat() if window.until else None,
    )
