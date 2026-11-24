from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .errors import ConfigError, OpenRouterError, OpenRouterNotFound
from .paths import auth_path, credentials_file

API = "https://openrouter.ai/api/v1"
API_KEY_ENV = "OPENROUTER_API_KEY"
MANAGEMENT_KEY_ENV = "OPENROUTER_MANAGEMENT_KEY"
Opener = Callable[..., Any]


def _auth_file_key() -> str | None:
    try:
        blob = json.loads(auth_path().read_text(encoding="utf-8"))["openrouter"]
        key = blob.get("key") if isinstance(blob, dict) else blob
    except (OSError, KeyError, ValueError, TypeError):
        return None
    return (key or "").strip() or None


def _credentials_value(name: str) -> str | None:
    """Read `[openrouter] <name>` from the project's credentials file."""
    try:
        blob = tomllib.loads(credentials_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    section = blob.get("openrouter")
    if not isinstance(section, dict):
        return None
    value = section.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def api_key_source() -> str | None:
    """Describe where the inference credential comes from without exposing it."""
    if (os.environ.get(API_KEY_ENV) or "").strip():
        return API_KEY_ENV
    if _credentials_value("inference_key"):
        return f"{credentials_file()} (inference_key)"
    if _auth_file_key():
        return str(auth_path())
    return None


def api_key() -> str | None:
    """Inference credential: env, then the credentials file, then pi's auth.json."""
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    return key or _credentials_value("inference_key") or _auth_file_key()


def management_key_source() -> str | None:
    """Describe where the write credential comes from without exposing it."""
    if (os.environ.get(MANAGEMENT_KEY_ENV) or "").strip():
        return MANAGEMENT_KEY_ENV
    if _credentials_value("management_key"):
        return f"{credentials_file()} (management_key)"
    return None


def management_key() -> str | None:
    """Write credential for workspace configuration (presets, keys, budgets).

    OpenRouter rejects inference keys on those routes, so this is deliberately
    a separate credential and never falls back to the inference key.
    """
    key = (os.environ.get(MANAGEMENT_KEY_ENV) or "").strip()
    return key or _credentials_value("management_key")


def _missing_write_key_message() -> str:
    return (
        "preset writes need a read-write management key: set "
        f'{MANAGEMENT_KEY_ENV} or add management_key = "..." under [openrouter] in '
        f"{credentials_file()} (OpenRouter rejects inference keys on these routes)"
    )


class OpenRouterClient:
    """Small injectable client so tests never need the network."""

    def __init__(
        self,
        base_url: str = API,
        *,
        timeout: float = 30,
        key: str | None = None,
        write_key: str | None = None,
        opener: Opener | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._key = key
        self._write_key = write_key
        self._opener = opener or urllib.request.urlopen

    def key(self) -> str | None:
        return self._key.strip() if isinstance(self._key, str) else api_key()

    def write_key(self) -> str | None:
        """Explicit write_key, else an explicitly injected key, else the env/file."""
        if isinstance(self._write_key, str):
            return self._write_key.strip() or None
        if isinstance(self._key, str):
            return self._key.strip() or None
        return management_key()

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = False,
        write: bool = False,
    ) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        key = self.write_key() if write else self.key()
        if auth:
            if not key:
                if write:
                    raise ConfigError(_missing_write_key_message())
                raise OpenRouterError(
                    f"no OpenRouter credentials: set {API_KEY_ENV} or add openrouter to {auth_path()}"
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
            if write and exc.code == 401:
                raise ConfigError(
                    f"{method} {path}: HTTP {exc.code} {exc.reason}\n{detail}\n"
                    f"{_missing_write_key_message()} "
                    f"(using {management_key_source() or 'no management key'})"
                ) from exc
            error = OpenRouterNotFound if exc.code == 404 else OpenRouterError
            raise error(f"{method} {path}: HTTP {exc.code} {exc.reason}\n{detail}") from exc
        except urllib.error.URLError as exc:
            raise OpenRouterError(f"{method} {path}: {exc.reason}") from exc
        except ValueError as exc:
            raise OpenRouterError(f"{method} {path}: invalid JSON response") from exc

    def get_json(self, path: str, *, auth: bool = False, write: bool = False) -> Any:
        return self._request("GET", path, auth=auth, write=write)

    def post_json(self, path: str, body: dict[str, Any], *, write: bool = True) -> Any:
        return self._request("POST", path, body, auth=True, write=write)

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
        return self.post_json(f"/presets/{slug}/chat/completions", config, write=True)

    def fetch_key_info(self, *, write: bool = False) -> dict[str, Any]:
        """Metadata for the credential in use (`is_management_key`, limits, ...)."""
        payload = self.get_json("/key", auth=True, write=write)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise OpenRouterError("/key: unexpected response")
        return data


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
