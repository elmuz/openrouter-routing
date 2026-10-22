from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .errors import ConfigError
from .fsutil import atomic_write_text

SERVICE_NAME = "openrouter-routing.service"
TIMER_NAME = "openrouter-routing.timer"
EXEC_START_PLACEHOLDER = "@EXEC_START@"

Runner = Callable[[Sequence[str]], int]

_SAFE_ARGUMENT = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/._-:@+")


@dataclass(frozen=True)
class UnitContents:
    service: str
    timer: str


@dataclass(frozen=True)
class InstallResult:
    unit_dir: Path
    service_path: Path
    timer_path: Path
    service_text: str
    timer_text: str
    wrote: bool = False
    enabled: bool = False
    commands: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class UninstallResult:
    unit_dir: Path
    service_path: Path
    timer_path: Path
    existing: bool = False
    disabled: bool = False
    removed: bool = False
    commands: tuple[tuple[str, ...], ...] = ()


def _template_dir() -> Path:
    """Find the tracked templates in a checkout or a built wheel."""
    packaged = Path(__file__).resolve().parent / "systemd"
    if packaged.is_dir():
        return packaged
    checkout = Path(__file__).resolve().parents[2] / "systemd"
    if checkout.is_dir():
        return checkout
    raise ConfigError("systemd unit templates are missing from this installation")


def _template(name: str) -> str:
    try:
        return (_template_dir() / name).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read systemd template {name}: {exc}") from exc


def _systemd_argument(value: str) -> str:
    if value and all(character in _SAFE_ARGUMENT for character in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def render_units(exec_start: Sequence[str]) -> UnitContents:
    if not exec_start:
        raise ConfigError("an ExecStart command is required")
    rendered = " ".join(_systemd_argument(part) for part in exec_start)
    service = _template(SERVICE_NAME)
    if EXEC_START_PLACEHOLDER not in service:
        raise ConfigError(f"{SERVICE_NAME} template has no {EXEC_START_PLACEHOLDER} placeholder")
    return UnitContents(
        service=service.replace(EXEC_START_PLACEHOLDER, rendered),
        timer=_template(TIMER_NAME),
    )


def resolve_unit_dir(value: str | Path | None = None) -> Path:
    directory = Path(value).expanduser() if value is not None else paths.systemd_user_dir()
    if not directory.is_absolute():
        directory = Path.cwd() / directory
    return directory.resolve(strict=False)


def _resolve_config_path(config_path: str | Path) -> Path:
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


def resolve_exec_start(binary: str | None, config_path: str | Path) -> tuple[str, ...]:
    """Build the command the timer runs.

    The default is the installed `openrouter-routing` entry point. In a source
    checkout without it on PATH, the repository's virtualenv binary or
    `python -m openrouter_routing` is used.
    """
    if binary:
        direct = Path(binary).expanduser()
        if direct.is_file():
            parts = (str(direct.resolve()),)
        else:
            try:
                parts = tuple(shlex.split(binary))
            except ValueError as exc:
                raise ConfigError(f"invalid --binary value {binary!r}: {exc}") from exc
            if not parts:
                raise ConfigError("--binary must not be empty")
            executable = parts[0]
            if "/" in executable or Path(executable).is_absolute():
                path = Path(executable).expanduser()
                if not path.is_absolute():
                    path = Path.cwd() / path
                path = path.resolve(strict=False)
                if not path.is_file():
                    raise ConfigError(f"--binary {path} does not exist")
                parts = (str(path), *parts[1:])
            else:
                found = shutil.which(executable)
                if found is None:
                    raise ConfigError(f"--binary {executable!r} was not found on PATH")
                parts = (found, *parts[1:])
    else:
        found = shutil.which("openrouter-routing")
        if found:
            parts = (found,)
        else:
            candidate = Path(sys.executable).with_name("openrouter-routing")
            parts = (str(candidate),) if candidate.is_file() else (sys.executable, "-m", "openrouter_routing")

    return (*parts, "refresh", "--apply", "--config", str(_resolve_config_path(config_path)))


def systemctl_command(*arguments: str) -> tuple[str, ...]:
    return ("systemctl", "--user", *arguments)


def run_command(command: Sequence[str]) -> int:
    try:
        completed = subprocess.run(command, check=False)
    except FileNotFoundError as exc:
        raise ConfigError(f"cannot run {command[0]}: {exc}") from exc
    return completed.returncode


def _run_checked(
    runner: Runner,
    command: Sequence[str],
    commands: list[tuple[str, ...]],
) -> None:
    rendered = " ".join(command)
    commands.append(tuple(command))
    status = runner(command)
    if status != 0:
        raise ConfigError(f"command failed with exit status {status}: {rendered}")


def install_systemd(
    *,
    config_path: str | Path,
    unit_dir: str | Path | None = None,
    binary: str | None = None,
    force: bool = False,
    enable: bool = False,
    print_only: bool = False,
    runner: Runner | None = None,
) -> InstallResult:
    if enable and print_only:
        raise ConfigError("use either --enable or --print, not both")

    directory = resolve_unit_dir(unit_dir)
    service_path = directory / SERVICE_NAME
    timer_path = directory / TIMER_NAME
    contents = render_units(resolve_exec_start(binary, config_path))
    result = InstallResult(
        unit_dir=directory,
        service_path=service_path,
        timer_path=timer_path,
        service_text=contents.service,
        timer_text=contents.timer,
    )
    if print_only:
        return result

    existing = [str(path) for path in (service_path, timer_path) if path.exists()]
    if existing and not force:
        raise ConfigError(f"{', '.join(existing)} already exists; use --force to overwrite")

    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(service_path, contents.service, mode=0o644)
    atomic_write_text(timer_path, contents.timer, mode=0o644)

    commands: list[tuple[str, ...]] = []
    if enable:
        run = runner or run_command
        _run_checked(run, systemctl_command("daemon-reload"), commands)
        _run_checked(run, systemctl_command("enable", "--now", TIMER_NAME), commands)

    return InstallResult(
        unit_dir=directory,
        service_path=service_path,
        timer_path=timer_path,
        service_text=contents.service,
        timer_text=contents.timer,
        wrote=True,
        enabled=enable,
        commands=tuple(commands),
    )


def uninstall_systemd(
    *,
    unit_dir: str | Path | None = None,
    remove: bool = False,
    print_only: bool = False,
    runner: Runner | None = None,
) -> UninstallResult:
    directory = resolve_unit_dir(unit_dir)
    service_path = directory / SERVICE_NAME
    timer_path = directory / TIMER_NAME
    timer_exists = timer_path.exists()
    service_exists = service_path.exists()
    exists = timer_exists or service_exists

    result = UninstallResult(
        unit_dir=directory,
        service_path=service_path,
        timer_path=timer_path,
        existing=exists,
    )
    if not exists:
        return result

    commands: list[tuple[str, ...]] = []
    planned: list[tuple[str, ...]] = []
    if timer_exists:
        planned.append(systemctl_command("disable", "--now", TIMER_NAME))
    if remove:
        planned.append(systemctl_command("daemon-reload"))
    if print_only:
        return UninstallResult(
            unit_dir=directory,
            service_path=service_path,
            timer_path=timer_path,
            existing=exists,
            commands=tuple(planned),
        )

    run = runner or run_command
    if timer_exists:
        _run_checked(run, planned[0], commands)
    disabled = timer_exists

    removed = False
    if remove:
        timer_path.unlink(missing_ok=True)
        service_path.unlink(missing_ok=True)
        _run_checked(run, systemctl_command("daemon-reload"), commands)
        removed = True

    return UninstallResult(
        unit_dir=directory,
        service_path=service_path,
        timer_path=timer_path,
        existing=exists,
        disabled=disabled,
        removed=removed,
        commands=tuple(commands),
    )
