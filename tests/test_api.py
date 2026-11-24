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


def write_credentials(tmp_path, **values) -> str:
    body = "[openrouter]\n" + "".join(f'{name} = "{value}"\n' for name, value in values.items())
    path = tmp_path / "credentials"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def capture_headers(payloads: dict[str, dict]):
    seen: list[tuple[str, str | None]] = []

    def opener(request, timeout=None):
        seen.append((request.full_url.split("/api/v1", 1)[-1], request.get_header("Authorization")))
        path = request.full_url.split("/api/v1", 1)[-1]
        return Response(json.dumps(payloads[path]).encode())

    return opener, seen


def test_inference_key_comes_from_credentials_file(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PI_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv(
        "OPENROUTER_ROUTING_CREDENTIALS", write_credentials(tmp_path, inference_key="sk-inference")
    )
    opener, seen = capture_headers({"/key": {"data": {"is_management_key": False}}})
    client = OpenRouterClient(opener=opener)
    assert client.key() == "sk-inference"
    client.fetch_key_info()
    assert seen[0][1] == "Bearer sk-inference"


def test_preset_writes_use_the_management_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PI_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv(
        "OPENROUTER_ROUTING_CREDENTIALS",
        write_credentials(tmp_path, inference_key="sk-inference", management_key="sk-management"),
    )
    opener, seen = capture_headers({"/presets/ds4/chat/completions": {"ok": True}})
    client = OpenRouterClient(opener=opener)
    client.post_preset_config("ds4", {"model": "vendor/model"})
    assert seen == [("/presets/ds4/chat/completions", "Bearer sk-management")]


def test_management_key_env_wins_over_credentials_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "OPENROUTER_ROUTING_CREDENTIALS", write_credentials(tmp_path, management_key="sk-file")
    )
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_KEY", "sk-env")
    assert OpenRouterClient().write_key() == "sk-env"


def test_preset_writes_fail_without_a_management_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PI_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("OPENROUTER_ROUTING_CREDENTIALS", str(tmp_path / "missing"))

    from openrouter_routing.errors import ConfigError

    client = OpenRouterClient(opener=lambda request, timeout=None: pytest.fail("must not call out"))
    with pytest.raises(ConfigError, match="read-write management key"):
        client.post_preset_config("ds4", {"model": "vendor/model"})


def test_rejected_write_key_reports_the_fix(monkeypatch, tmp_path) -> None:
    import urllib.error

    from openrouter_routing.errors import ConfigError

    monkeypatch.setenv(
        "OPENROUTER_ROUTING_CREDENTIALS", write_credentials(tmp_path, management_key="sk-wrong")
    )

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"Invalid management key"}')
        )

    client = OpenRouterClient(opener=opener)
    with pytest.raises(ConfigError, match="management key"):
        client.post_preset_config("ds4", {"model": "vendor/model"})
