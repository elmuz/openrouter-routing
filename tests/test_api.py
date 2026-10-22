from __future__ import annotations

import io
import json

import pytest

from openrouter_routing.api import OpenRouterClient, resolve_model
from openrouter_routing.errors import OpenRouterError


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def opener_for(payloads: dict[str, dict], calls: list[str] | None = None):
    def opener(request, timeout=None):
        calls.append(request.full_url) if calls is not None else None
        path = request.full_url.split("/api/v1", 1)[-1]
        return Response(json.dumps(payloads[path]).encode())

    return opener


def test_client_fetches_endpoints_without_key() -> None:
    client = OpenRouterClient(
        key="",
        opener=opener_for({"/models/vendor/model/endpoints": {"data": {"endpoints": [{"tag": "x"}]}}}),
    )
    assert client.fetch_endpoints("vendor/model")["endpoints"] == [{"tag": "x"}]


def test_resolve_model_follows_alias() -> None:
    calls: list[str] = []
    client = OpenRouterClient(
        key="",
        opener=opener_for(
            {
                "/models/~vendor/latest/endpoints": {"data": {"endpoints": []}},
                "/models/vendor/concrete/endpoints": {"data": {"endpoints": [{"tag": "px/fp8"}]}},
                "/models": {"data": [{"id": "~vendor/latest", "alias_target": {"slug": "vendor/concrete"}}]},
            },
            calls,
        ),
    )
    model, endpoints = resolve_model("~vendor/latest", client)
    assert model == "vendor/concrete"
    assert endpoints == [{"tag": "px/fp8"}]
    assert calls[0].endswith("/models/~vendor/latest/endpoints")


def test_auth_required_without_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PI_AGENT_DIR", "/nonexistent")
    client = OpenRouterClient(key="")
    with pytest.raises(OpenRouterError):
        client.get_json("/presets/x", auth=True)


def test_http_error_is_wrapped() -> None:
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error":"nope"}'))

    client = OpenRouterClient(key="", opener=opener)
    with pytest.raises(OpenRouterError, match="HTTP 404"):
        client.get_json("/models/x/endpoints")


def test_fetch_generation_requires_and_returns_data() -> None:
    client = OpenRouterClient(
        key="secret",
        opener=opener_for({"/generation?id=gen-1": {"data": {"id": "gen-1", "total_cost": 0.5}}}),
    )
    generation = client.fetch_generation("gen-1")
    assert generation["id"] == "gen-1"
    assert generation["total_cost"] == 0.5


def test_fetch_generation_404_is_not_found() -> None:
    import urllib.error

    from openrouter_routing.errors import OpenRouterNotFound

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b'{"error":"gone"}'))

    client = OpenRouterClient(key="secret", opener=opener)
    with pytest.raises(OpenRouterNotFound, match="HTTP 404"):
        client.fetch_generation("gen-gone")
