from __future__ import annotations

from dataclasses import dataclass

from .api import OpenRouterClient, api_key_source, management_key_source
from .config import AppConfig
from .sessions import SessionSource
from .targets import PiModelTarget, PresetTarget, open_target


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    warning: bool = False


def _error_check(name: str, exc: Exception) -> Check:
    return Check(name, False, f"{type(exc).__name__}: {exc}")


def _write_credential_check(cfg: AppConfig, client: OpenRouterClient) -> Check:
    """Preset writes need a management key; inference keys get a 401."""
    name = "auth write"
    source = management_key_source()
    needs_write = any(target.kind == "preset" for target in cfg.targets)
    if not source:
        detail = (
            "no management key; refresh --apply will fail with 401 on preset targets "
            "(inference keys are rejected for preset writes)"
        )
        return Check(name, True, detail, warning=needs_write)
    fetch_key_info = getattr(client, "fetch_key_info", None)
    if fetch_key_info is None:  # pragma: no cover - minimal test doubles
        return Check(name, True, source)
    try:
        info = fetch_key_info(write=True)
    except Exception as exc:  # doctor reports, never aborts
        return _error_check(name, exc)
    if not info.get("is_management_key"):
        return Check(
            name,
            False,
            f"{source} is not a management key; preset writes need a read-write management key",
        )
    scope = "provisioning key (account scope)" if info.get("is_provisioning_key") else "management key"
    return Check(name, True, f"{source} — {scope}")


def run_doctor(
    cfg: AppConfig,
    *,
    client: OpenRouterClient,
    source: SessionSource,
) -> list[Check]:
    checks: list[Check] = []

    config_path = cfg.source_path
    if config_path is None:  # pragma: no cover - load_config always sets one
        checks.append(Check("config", True, "defaults"))
    elif cfg.loaded:
        checks.append(Check("config", True, str(config_path)))
    else:
        checks.append(Check("config", True, f"{config_path} (missing; using defaults)", warning=True))

    if cfg.paths.pi_agent_dir.exists():
        checks.append(Check("pi agent", True, str(cfg.paths.pi_agent_dir)))
    else:
        checks.append(Check("pi agent", True, f"{cfg.paths.pi_agent_dir} (missing)", warning=True))

    try:
        paths = source.discover()
        if not paths:
            checks.append(Check("sessions", True, f"no files under {cfg.paths.sessions_glob}", warning=True))
        else:
            records = list(source.iter_records(paths))
            checks.append(Check("sessions", True, f"{len(paths)} file(s), {len(records)} usage record(s)"))
    except Exception as exc:  # doctor should report, never abort
        checks.append(_error_check("sessions", exc))

    auth = api_key_source()
    if auth:
        checks.append(Check("auth", True, auth))
    else:
        checks.append(
            Check(
                "auth",
                True,
                "no OPENROUTER_API_KEY and no usable auth.json entry; "
                "endpoint performance stats will be null",
                warning=True,
            )
        )

    checks.append(_write_credential_check(cfg, client))

    try:
        models = client.fetch_models_catalog()
        checks.append(Check("network", True, f"OpenRouter /models reachable ({len(models)} models)"))
    except Exception as exc:
        checks.append(_error_check("network", exc))

    if not cfg.targets:
        checks.append(
            Check(
                "targets",
                True,
                "none configured; refresh needs at least one [[target]]",
                warning=True,
            )
        )
    for target in cfg.targets:
        name = f"target {target.name}"
        try:
            backend = open_target(target, client)
            model = backend.model()
        except Exception as exc:
            checks.append(_error_check(name, exc))
            continue
        if isinstance(backend, PresetTarget):
            detail = (
                f"preset {target.preset} v{backend.version}, model={model}, "
                f"order={list(backend.current_order())}"
            )
            if backend.system_prompt:
                checks.append(Check(name, True, f"{detail}; has a system_prompt", warning=True))
            else:
                checks.append(Check(name, True, detail))
        elif isinstance(backend, PiModelTarget):
            checks.append(
                Check(
                    name,
                    True,
                    f"{backend.path} {target.pi_model_id}, model={model}, "
                    f"routing keys={sorted(backend.routing)}",
                )
            )
    return checks


def failed(checks: list[Check]) -> bool:
    return any(not check.ok for check in checks)


def format_check(check: Check) -> str:
    state = "fail" if not check.ok else ("warn" if check.warning else "ok")
    return f"{check.name:14} {state:4}  {check.detail}"
