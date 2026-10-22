from __future__ import annotations

import json
from pathlib import Path

import pytest

from openrouter_routing import cli
from openrouter_routing.errors import ConfigError, OpenRouterNotFound
from openrouter_routing.sessions import load_records
from openrouter_routing.verify import select_verifiable, verify_generations

FIXTURES = Path(__file__).parent / "fixtures" / "verify"


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self) -> None:
        self.generations = load_json("generations.json")
        self.endpoints = load_json("endpoints.json")
        self.generation_calls: list[str] = []
        self.endpoint_calls: list[str] = []

    def fetch_generation(self, generation_id: str):
        self.generation_calls.append(generation_id)
        if generation_id not in self.generations:
            raise OpenRouterNotFound(f"generation {generation_id} not found")
        return self.generations[generation_id]

    def fetch_endpoints(self, model: str):
        self.endpoint_calls.append(model)
        return self.endpoints


def records():
    return load_records([FIXTURES / "session.jsonl"])


def test_select_verifiable_deduplicates_and_orders(tmp_path) -> None:
    selected = select_verifiable(records())
    assert [record.response_id for record in selected] == [
        "gen-unmatched",
        "gen-missing",
        "gen-mismatch",
        "gen-ok",
    ]
    assert select_verifiable(records(), last=1)[0].response_id == "gen-unmatched"
    assert select_verifiable(records(), projects=["/home/alice/projects/verify"]) == selected
    assert select_verifiable(records(), projects=["/home/alice/projects/other"]) == []
    assert select_verifiable(records(), models=["@preset/main"]) == selected
    with pytest.raises(ConfigError, match="--last"):
        select_verifiable(records(), last=0)


def test_verify_compares_predicted_and_actual_cost() -> None:
    client = FakeClient()
    result = verify_generations(records(), client=client)
    by_id = {check.generation_id: check for check in result.checks}

    ok = by_id["gen-ok"]
    assert ok.status == "ok"
    assert ok.endpoint == "a/fp8"
    assert ok.predicted_cost == pytest.approx(0.00021)
    assert ok.actual_cost == pytest.approx(0.00021)
    assert ok.cached_tokens == 900
    assert ok.flags == ()

    mismatch = by_id["gen-mismatch"]
    assert mismatch.status == "mismatch"
    assert mismatch.endpoint == "b/fp8"
    assert mismatch.predicted_cost == pytest.approx(0.00032125)
    assert mismatch.actual_cost == pytest.approx(0.0004)
    assert any("cost mismatch" in flag for flag in mismatch.flags)
    assert any("generation cached 0 vs pi 800" in flag for flag in mismatch.flags)
    assert any("no cache discount" in flag for flag in mismatch.flags)

    assert by_id["gen-missing"].status == "missing"
    assert "expired" in (by_id["gen-missing"].error or "")
    assert by_id["gen-unmatched"].status == "unmatched"

    summary = result.summary
    assert summary.selected == 4
    assert summary.verified == 2
    assert summary.missing == 1
    assert summary.unmatched == 1
    assert summary.mismatches == 1
    assert summary.input == 300
    assert summary.cache_read == 1700
    assert summary.cache_write == 5
    assert summary.output == 30
    assert summary.total_input == 2000
    assert summary.f == pytest.approx(0.85)
    assert summary.R == pytest.approx(0.015)
    assert summary.fw == pytest.approx(0.0025)
    assert summary.predicted_c_eff == pytest.approx(0.265625)
    assert summary.actual_c_eff == pytest.approx(0.305)
    assert summary.providers == {"A": 1, "B": 1}
    assert client.endpoint_calls == ["vendor/model"]


def test_verify_filters_and_tolerance() -> None:
    result = verify_generations(records(), client=FakeClient(), last=2)
    assert [check.generation_id for check in result.checks] == ["gen-unmatched", "gen-missing"]
    assert result.summary.verified == 0

    result = verify_generations(records(), client=FakeClient(), last=4, tolerance=0.5)
    assert result.summary.mismatches == 0

    with pytest.raises(ConfigError, match="tolerance"):
        verify_generations(records(), client=FakeClient(), tolerance=1)


def test_verify_json_and_text_cli(tmp_path, monkeypatch, capsys) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nsessions_glob = "{FIXTURES / "session.jsonl"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)

    assert cli.main(["verify", "--config", str(config), "--last", "4"]) == 0
    out = capsys.readouterr().out
    assert "generations: 2 verified" in out
    assert "C_eff" in out
    assert "mismatch" in out

    assert cli.main(["verify", "--config", str(config), "--last", "4", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "openrouter-routing/verify/v1"
    assert payload["summary"]["verified"] == 2
    assert payload["summary"]["mismatches"] == 1
    assert [item["generation_id"] for item in payload["records"]][0] == "gen-unmatched"


def test_verify_cli_requires_response_ids(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        '{"type":"session","id":"s","cwd":"/tmp/x"}\n'
        '{"type":"message","id":"m","message":{"provider":"openrouter","model":"m",'
        '"usage":{"input":1,"cacheRead":1,"output":1,"cacheWrite":0}}}\n',
        encoding="utf-8",
    )
    config = tmp_path / "config.toml"
    config.write_text(f'[paths]\nsessions_glob = "{path}"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["verify", "--config", str(config)]) == 1
    assert "responseId" in capsys.readouterr().err
