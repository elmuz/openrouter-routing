from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .errors import OpenRouterError, OpenRouterNotFound
from .paths import auth_path

API = "https://openrouter.ai/api/v1"
Opener = Callable[..., Any]


def _auth_file_key() -> str | None:
    try:
        blob = json.loads(auth_path().read_text(encoding="utf-8"))["openrouter"]
        key = blob.get("key") if isinstance(blob, dict) else blob
    except (OSError, KeyError, ValueError, TypeError):
        return None
    return (key or "").strip() or None


def api_key_source() -> str | None:
    """Describe where credentials come from without exposing the key."""
    if (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        return "OPENROUTER_API_KEY"
    if _auth_file_key():
        return str(auth_path())
    return None


def api_key() -> str | None:
    """OPENROUTER_API_KEY, else the key stored by pi in auth.json."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        key = _auth_file_key()
    return (key or "").strip() or None


class OpenRouterClient:
    """Small injectable client so tests never need the network."""

    def __init__(
        self,
        base_url: str = API,
        *,
        timeout: float = 30,
        key: str | None = None,
        opener: Opener | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._key = key
        self._opener = opener or urllib.request.urlopen

    def key(self) -> str | None:
        return self._key.strip() if isinstance(self._key, str) else api_key()

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, auth: bool = False
    ) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        key = self.key()
        if auth:
            if not key:
                raise OpenRouterError(
                    f"no OpenRouter credentials: set OPENROUTER_API_KEY or add openrouter to {auth_path()}"
                )
            req.add_header("Authorization", f"Bearer {key}")
        elif key:
            # Endpoint stats are only returned for authenticated calls.
            req.add_header("Authorization", f"Bearer {key}")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener(req, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            error = OpenRouterNotFound if exc.code == 404 else OpenRouterError
            raise error(f"{method} {path}: HTTP {exc.code} {exc.reason}\n{detail}") from exc
        except urllib.error.URLError as exc:
            raise OpenRouterError(f"{method} {path}: {exc.reason}") from exc
        except ValueError as exc:
            raise OpenRouterError(f"{method} {path}: invalid JSON response") from exc

    def get_json(self, path: str, *, auth: bool = False) -> Any:
        return self._request("GET", path, auth=auth)

    def post_json(self, path: str, body: dict[str, Any], *, auth: bool = True) -> Any:
        return self._request("POST", path, body, auth=auth)

    def fetch_endpoints(self, model: str) -> dict[str, Any]:
        payload = self.get_json(f"/models/{model}/endpoints")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise OpenRouterError(f"/models/{model}/endpoints: unexpected response")
        return data

    def fetch_models_catalog(self) -> list[dict[str, Any]]:
        payload = self.get_json("/models")
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    def fetch_generation(self, generation_id: str) -> dict[str, Any]:
        """Return one generation record; raises OpenRouterNotFound when expired."""
        query = urlencode({"id": generation_id})
        payload = self.get_json(f"/generation?{query}", auth=True)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise OpenRouterError(f"/generation?id={generation_id}: unexpected response")
        return data

    def get_preset(self, slug: str) -> dict[str, Any]:
        payload = self.get_json(f"/presets/{slug}", auth=True)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise OpenRouterError(f"/presets/{slug}: unexpected response")
        return data

    def get_versions(self, slug: str) -> list[dict[str, Any]]:
        payload = self.get_json(f"/presets/{slug}/versions", auth=True)
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, list) else []

    def post_preset_config(self, slug: str, config: dict[str, Any]) -> Any:
        return self.post_json(f"/presets/{slug}/chat/completions", config, auth=True)


def resolve_model(model: str, client: OpenRouterClient) -> tuple[str, list[dict[str, Any]]]:
    """Resolve `~author/model-latest` aliases to a model that exposes endpoints."""
    current = model
    for _ in range(3):
        endpoints = client.fetch_endpoints(current).get("endpoints") or []
        if endpoints:
            return current, endpoints
        target = None
        for entry in client.fetch_models_catalog():
            if entry.get("id") == current:
                target = (entry.get("alias_target") or {}).get("slug")
                break
        if not target or target == current:
            return current, []
        current = target
    return current, []


def auth_file_for_display() -> Path:
    return auth_path()
