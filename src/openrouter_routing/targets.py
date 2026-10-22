from __future__ import annotations

import copy
import json
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .api import OpenRouterClient
from .config import RankConfig, TargetConfig
from .errors import ConfigError, PlanError
from .fsutil import atomic_write_text, backup_file, file_mode
from .preset import cfg_provider, model_of, new_config
from .pricing import EndpointScore


@dataclass(frozen=True)
class TargetPlan:
    target: TargetConfig
    model: str
    resolved_model: str
    mode: str
    mix: dict[str, Any]
    rows: list[EndpointScore]
    current_routing: dict[str, Any]
    proposed_routing: dict[str, Any]
    payload: dict[str, Any]
    current_order: tuple[str, ...]
    proposed_order: tuple[str, ...]
    changed: bool
    system_prompt: bool = False


def _orders(routing: dict[str, Any]) -> tuple[str, ...]:
    order = routing.get("order")
    return tuple(str(item) for item in order) if isinstance(order, list) else ()


def _plan_options(rank: RankConfig, *, mode: str, model: str | None) -> Namespace:
    return Namespace(
        model=model,
        sort=None,
        ignore=None,
        quant=rank.quant,
        allow_fallbacks=None,
        require_cache=rank.require_cache,
        min_tps=rank.min_tps,
        max_ttft=rank.max_ttft,
        tps_pct=rank.tps_pct,
        ttft_pct=rank.ttft_pct,
        prefer=rank.prefer,
        top=rank.top,
        routing_mode=mode,
        # Keep the full filtered set for --compare; provider.order still uses top.
        keep_all_rows=True,
    )


class PresetTarget:
    """Preset backend: keeps the existing full-config POST semantics."""

    def __init__(self, target: TargetConfig, client: OpenRouterClient) -> None:
        if not target.preset:
            raise ConfigError(f"target {target.name!r}: kind='preset' requires preset = '<slug>'")
        self.target = target
        self.client = client
        self.preset = client.get_preset(target.preset)
        designated = self.preset.get("designated_version") or {}
        config = designated.get("config")
        if not isinstance(config, dict) or not config:
            raise PlanError(f"preset {target.preset}: designated version has no config")
        self.config = copy.deepcopy(config)
        self.version = designated.get("version")
        self.system_prompt = bool(designated.get("system_prompt"))

    def model(self) -> str:
        return self.target.model or model_of(self.config)

    def current_order(self) -> tuple[str, ...]:
        return _orders(cfg_provider(self.config))

    def plan(self, mix: dict[str, Any], rank: RankConfig, mode: str) -> TargetPlan:
        options = _plan_options(rank, mode=mode, model=self.target.model)
        new, rows, resolved = new_config(self.config, mix, options, self.client)
        current_routing = copy.deepcopy(cfg_provider(self.config))
        proposed_routing = copy.deepcopy(cfg_provider(new))
        return TargetPlan(
            target=self.target,
            model=self.model(),
            resolved_model=resolved,
            mode=mode,
            mix=mix,
            rows=rows,
            current_routing=current_routing,
            proposed_routing=proposed_routing,
            payload=new,
            current_order=_orders(current_routing),
            proposed_order=_orders(proposed_routing),
            changed=new != self.config,
            system_prompt=self.system_prompt,
        )

    def apply(self, plan: TargetPlan) -> Path | None:
        if not plan.changed:
            return None
        self.client.post_preset_config(self.target.preset or "", plan.payload)
        return None


class PiModelTarget:
    """Local pi backend: patch `compat.openRouterRouting` in models.json."""

    def __init__(self, target: TargetConfig, client: OpenRouterClient) -> None:
        if not target.pi_model_id:
            raise ConfigError(f"target {target.name!r}: kind='pi-model' requires pi_model_id")
        self.target = target
        self.client = client
        self.path = target.pi_models_json or paths.models_json_path()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigError(f"target {target.name!r}: {self.path} does not exist") from exc
        except OSError as exc:
            raise ConfigError(f"target {target.name!r}: cannot read {self.path}: {exc}") from exc
        try:
            document = json.loads(raw)
        except ValueError as exc:
            raise ConfigError(f"target {target.name!r}: invalid JSON in {self.path}: {exc}") from exc
        if not isinstance(document, dict):
            raise ConfigError(f"target {target.name!r}: {self.path} must contain a JSON object")
        providers = document.get("providers")
        if not isinstance(providers, dict):
            raise ConfigError(f"target {target.name!r}: {self.path} has no providers object")
        provider = providers.get(target.pi_provider)
        if not isinstance(provider, dict):
            raise ConfigError(
                f"target {target.name!r}: provider {target.pi_provider!r} not found in {self.path}"
            )
        models = provider.get("models")
        if not isinstance(models, list):
            raise ConfigError(
                f"target {target.name!r}: provider {target.pi_provider!r} has no models list in {self.path}"
            )
        entry = next(
            (item for item in models if isinstance(item, dict) and item.get("id") == target.pi_model_id),
            None,
        )
        if entry is None:
            raise ConfigError(
                f"target {target.name!r}: model {target.pi_model_id!r} not found in"
                f" {self.path} providers.{target.pi_provider}.models"
            )
        compat = entry.get("compat") or {}
        if not isinstance(compat, dict):
            raise ConfigError(f"target {target.name!r}: {target.pi_model_id}.compat must be an object")
        routing = compat.get("openRouterRouting") or {}
        if not isinstance(routing, dict):
            raise ConfigError(
                f"target {target.name!r}: {target.pi_model_id}.compat.openRouterRouting must be an object"
            )
        self.document = document
        self.entry = entry
        self.routing = copy.deepcopy(routing)

    def model(self) -> str:
        if not self.target.model:
            raise ConfigError(
                f"target {self.target.name!r}: kind='pi-model' requires an OpenRouter model = '...'"
            )
        return self.target.model

    def current_order(self) -> tuple[str, ...]:
        return _orders(self.routing)

    def plan(self, mix: dict[str, Any], rank: RankConfig, mode: str) -> TargetPlan:
        model = self.model()
        pseudo = {"model": model, "provider": copy.deepcopy(self.routing)}
        options = _plan_options(rank, mode=mode, model=model)
        new, rows, resolved = new_config(pseudo, mix, options, self.client)
        proposed = copy.deepcopy(cfg_provider(new))
        return TargetPlan(
            target=self.target,
            model=model,
            resolved_model=resolved,
            mode=mode,
            mix=mix,
            rows=rows,
            current_routing=copy.deepcopy(self.routing),
            proposed_routing=proposed,
            payload=proposed,
            current_order=_orders(self.routing),
            proposed_order=_orders(proposed),
            changed=proposed != self.routing,
        )

    def apply(self, plan: TargetPlan) -> Path | None:
        if not plan.changed:
            return None
        compat = self.entry.setdefault("compat", {})
        if not isinstance(compat, dict):
            raise ConfigError(f"target {self.target.name!r}: compat must be an object")
        compat["openRouterRouting"] = plan.payload
        text = json.dumps(self.document, indent=2) + "\n"
        try:
            json.loads(text)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ConfigError(f"target {self.target.name!r}: refusing to write invalid JSON") from exc
        backup = backup_file(self.path)
        atomic_write_text(self.path, text, mode=file_mode(self.path))
        return backup


def open_target(target: TargetConfig, client: OpenRouterClient) -> PresetTarget | PiModelTarget:
    if target.kind == "preset":
        return PresetTarget(target, client)
    if target.kind == "pi-model":
        return PiModelTarget(target, client)
    raise ConfigError(f"target {target.name!r}: unknown kind {target.kind!r}")
