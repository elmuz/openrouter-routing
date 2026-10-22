from __future__ import annotations

import json
from argparse import Namespace

import pytest

from openrouter_routing import cli


def set_missing_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("OPENROUTER_ROUTING_CONFIG", str(tmp_path / "missing.toml"))


def test_measure_json(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--json", *session_paths]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["requests"] == 6
    assert payload["f"] == pytest.approx(1210 / 1450)
    assert payload["schema"] == "openrouter-routing/mix/v1"


def test_measure_project_filter_and_grouping(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    code = cli.main(
        [
            "measure",
            "--json",
            "--project",
            "/home/alice/projects/alpha",
            "--by",
            "model",
            *session_paths,
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["requests"] == 3
    assert set(payload["groups"]["model"]) == {"@preset/main", "@preset/other"}


def test_measure_hours_uses_latest_record(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--json", "--hours", "12", *session_paths]) == 0
    assert json.loads(capsys.readouterr().out)["requests"] == 3


def test_config_init_path_and_show(monkeypatch, tmp_path, capsys) -> None:
    config = tmp_path / "config.toml"
    monkeypatch.setenv("OPENROUTER_ROUTING_CONFIG", str(config))
    assert cli.main(["config", "init"]) == 0
    assert config.exists()
    capsys.readouterr()
    assert cli.main(["config", "path"]) == 0
    assert str(config) in capsys.readouterr().out
    assert cli.main(["config", "show"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["loaded"] is True
    assert payload["rank"]["top"] == 4


class FakeClient:
    def fetch_endpoints(self, model):
        return {
            "endpoints": [
                {
                    "tag": "a/fp8",
                    "provider_name": "A",
                    "pricing": {
                        "prompt": "0.000001",
                        "completion": "0.000002",
                        "input_cache_read": "0.0000001",
                    },
                    "quantization": "fp8",
                    "status": 0,
                    "latency_last_30m": {"p90": 1000},
                    "throughput_last_30m": {"p90": 100},
                },
                {
                    "tag": "b/fp8",
                    "provider_name": "B",
                    "pricing": {
                        "prompt": "0.000002",
                        "completion": "0.000003",
                        "input_cache_read": "0.0000002",
                    },
                    "quantization": "fp8",
                    "status": 0,
                    "latency_last_30m": {"p90": 1000},
                    "throughput_last_30m": {"p90": 100},
                },
            ]
        }

    def fetch_models_catalog(self):
        return []


def test_cheapest_order(monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["cheapest", "vendor/model", "--order"]) == 0
    assert json.loads(capsys.readouterr().out) == ["a/fp8", "b/fp8"]


def test_cheapest_requires_healthy(capsys, monkeypatch, tmp_path) -> None:
    set_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["cheapest", "vendor/model", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cheapest"] == "a/fp8"


def test_rank_defaults_come_from_config(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[rank]
top = 2
healthy_only = true
min_tps = 40
tps_pct = "p75"
""",
        encoding="utf-8",
    )
    cfg = cli.load_config(config)
    args = Namespace(
        healthy_only=None,
        require_cache=None,
        prefer=None,
        quant=None,
        min_tps=None,
        max_ttft=None,
        tps_pct=None,
        ttft_pct=None,
        top=None,
        max_price_from=None,
    )
    cli._rank_defaults(args, cfg)
    assert args.top == 2
    assert args.healthy_only is True
    assert args.min_tps == 40
    assert args.tps_pct == "p75"
    assert args.require_cache is False
    assert args.max_price_from == 0
