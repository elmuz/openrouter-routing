from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def test_measure_last_sessions(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--json", "--last-sessions", "1", *session_paths]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["requests"] == 3
    assert payload["window"]["last_sessions"] == 1


def test_measure_anchor_now_uses_wall_clock(tmp_path, monkeypatch, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    now = datetime.now(UTC)
    path = tmp_path / "--tmp-anchor--" / "s.jsonl"
    path.parent.mkdir()
    lines = ['{"type":"session","id":"s","cwd":"/tmp/anchor"}']
    for message_id, when in (("old", now - timedelta(hours=3)), ("new", now - timedelta(hours=1))):
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "id": message_id,
                    "timestamp": when.isoformat(),
                    "message": {
                        "provider": "openrouter",
                        "model": "m",
                        "usage": {"input": 1, "cacheRead": 9, "output": 1, "cacheWrite": 0},
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert cli.main(["measure", "--json", "--anchor", "now", "--hours", "1.5", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["requests"] == 1
    assert cli.main(["measure", "--json", "--anchor", "latest", "--hours", "3", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["requests"] == 2


def test_measure_provider_and_exclude_project(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--json", "--provider", "openrouter", *session_paths]) == 0
    assert json.loads(capsys.readouterr().out)["requests"] == 6

    assert cli.main(["measure", "--json", "--provider", "nope", *session_paths]) == 1
    assert "no usage records" in capsys.readouterr().err

    assert (
        cli.main(["measure", "--json", "--exclude-project", "/home/alice/projects/alpha", *session_paths])
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["requests"] == 3
    assert payload["filters"]["exclude_projects"] == ["/home/alice/projects/alpha"]


def test_measure_top_projects_and_root_validation(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--json", "--top-projects", "1", *session_paths]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["top_projects"]) == 1
    assert payload["top_projects"][0]["name"] == "/home/alice/projects/alpha"
    assert payload["top_projects"][0]["total_input"] == 1150

    assert cli.main(["measure", "--json", "--project", "/", *session_paths]) == 1
    assert "filesystem root" in capsys.readouterr().err


def test_measure_tsv_quiet_and_verbose(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--format", "tsv", "--by", "project", *session_paths]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("scope\tkey\tname")
    assert "overall\t\tall" in out
    assert "group\tproject\t/home/alice/projects/alpha" in out

    assert cli.main(["measure", "--quiet", *session_paths]) == 0
    assert "tip:" not in capsys.readouterr().err

    assert cli.main(["measure", "--json", "--verbose", *session_paths]) == 0
    assert "config" in capsys.readouterr().err


def test_measure_groups_are_deterministic(alpha_path, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--json", "--by", "model", alpha_path]) == 0
    first = json.loads(capsys.readouterr().out)
    assert list(first["groups"]["model"]) == ["@preset/main", "@preset/other"]
    assert cli.main(["measure", "--json", "--by", "model", alpha_path]) == 0
    assert json.loads(capsys.readouterr().out) == first


def test_measure_text_prints_resolved_window(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["measure", "--since", "2026-01-01T10:00:02.000Z", *session_paths]) == 0
    out = capsys.readouterr().out
    assert "2026-01-01 10:00:02" in out
    assert "explicit bounds (inclusive)" in out


def test_measure_cost_model(session_paths, monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "OpenRouterClient", FakeClient)
    assert cli.main(["measure", "--json", "--cost-model", "vendor/model", *session_paths]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cost_model"]["endpoint"] == "a/fp8"
    assert payload["estimated_cost_usd"] > 0
    assert all(group["estimated_cost_usd"] > 0 for group in payload["groups"]["provider-model"].values())


class FakePresetClient(FakeClient):
    posted: list[tuple[str, dict]] = []
    preset_config = {"model": "vendor/model", "provider": {"order": ["old/fp8"]}}
    system_prompt = ""

    def __init__(self) -> None:
        type(self).posted = []

    def get_preset(self, slug):
        return {
            "slug": slug,
            "designated_version": {
                "version": 1,
                "config": self.preset_config,
                "system_prompt": self.system_prompt,
            },
        }

    def post_preset_config(self, slug, config):
        type(self).posted.append((slug, config))
        return {}


def test_cheapest_compare_current(monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "OpenRouterClient", FakePresetClient)
    monkeypatch.setattr(
        FakePresetClient,
        "preset_config",
        {"model": "vendor/model", "provider": {"order": ["b/fp8"]}},
    )

    assert cli.main(["cheapest", "--preset", "p", "--compare-current", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"] == "vendor/model"
    comparison = payload["comparison"]
    assert comparison["current_order"] == ["b/fp8"]
    assert comparison["current"]["endpoint"] == "b/fp8"
    assert comparison["proposed"]["endpoint"] == "a/fp8"
    assert comparison["delta_pct"] < 0

    assert cli.main(["cheapest", "--preset", "p", "--compare-current"]) == 0
    out = capsys.readouterr().out
    assert "current order: b/fp8" in out
    assert "proposed : a/fp8" in out
    assert "mix cost" in out


def test_cheapest_compare_current_requires_preset(monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    assert cli.main(["cheapest", "vendor/model", "--compare-current"]) == 1
    assert "requires --preset" in capsys.readouterr().err


class FakeDoctorClient:
    def fetch_models_catalog(self):
        return [{"id": "a"}]


class FailingDoctorClient:
    def fetch_models_catalog(self):
        raise RuntimeError("offline")


def write_refresh_config(tmp_path, session_paths, *, kind="preset", pi_model=None) -> Path:
    sessions = Path(session_paths[0]).parent.parent / "**" / "*.jsonl"
    target = f'''
[[target]]
name = "local"
kind = "{kind}"
model = "vendor/model"
'''
    if kind == "preset":
        target += 'preset = "p"\n'
    else:
        target += f'pi_models_json = "{pi_model}"\npi_model_id = "@preset/main"\n'
    config = tmp_path / "config.toml"
    config.write_text(
        f'''
[paths]
sessions_glob = "{sessions}"

[refresh]
days = 365
anchor = "latest"
{target}
''',
        encoding="utf-8",
    )
    return config


def test_preset_plan_preferred_mode(monkeypatch, tmp_path, capsys) -> None:
    set_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "OpenRouterClient", FakePresetClient)
    assert cli.main(["preset", "plan", "p", "--routing-mode", "preferred"]) == 0
    out = capsys.readouterr().out
    assert '"by": "price"' in out
    assert '"order"' not in out
    assert "dry run" in out
    assert FakePresetClient.posted == []


def test_refresh_cli_dry_run_and_apply(session_paths, monkeypatch, tmp_path, capsys) -> None:
    config = write_refresh_config(tmp_path, session_paths)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "OpenRouterClient", FakePresetClient)

    assert cli.main(["refresh", "--config", str(config)]) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "old order: old/fp8" in out
    assert "new order: a/fp8" in out
    assert "dry run" in out
    assert "sticky routing" in captured.err
    assert FakePresetClient.posted == []
    assert not (tmp_path / "state" / "openrouter-routing" / "state.json").exists()

    log = tmp_path / "refresh.log"
    assert cli.main(["refresh", "--config", str(config), "--apply", "--compare", "--log-file", str(log)]) == 0
    out = capsys.readouterr().out
    assert "applied local" in out
    assert "compare  :" in out
    assert FakePresetClient.posted
    assert "target local: applied" in log.read_text(encoding="utf-8")
    state = tmp_path / "state" / "openrouter-routing" / "state.json"
    assert state.exists()


def test_refresh_cli_warns_about_preset_system_prompt(session_paths, monkeypatch, tmp_path, capsys) -> None:
    config = write_refresh_config(tmp_path, session_paths)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "OpenRouterClient", FakePresetClient)
    monkeypatch.setattr(FakePresetClient, "system_prompt", "be careful")
    assert cli.main(["refresh", "--config", str(config)]) == 0
    captured = capsys.readouterr()
    assert "system_prompt" in captured.err


def test_refresh_cli_rejects_apply_with_dry_run(session_paths, monkeypatch, tmp_path, capsys) -> None:
    config = write_refresh_config(tmp_path, session_paths)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert cli.main(["refresh", "--config", str(config), "--apply", "--dry-run"]) == 1
    assert "not both" in capsys.readouterr().err


def test_doctor_cli(session_paths, monkeypatch, tmp_path, capsys) -> None:
    models = tmp_path / "models.json"
    models.write_text(
        json.dumps(
            {
                "providers": {
                    "openrouter": {
                        "models": [
                            {
                                "id": "@preset/main",
                                "compat": {"openRouterRouting": {"order": ["a/fp8"]}},
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = write_refresh_config(tmp_path, session_paths, kind="pi-model", pi_model=models)
    monkeypatch.setenv("PI_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setattr(cli, "OpenRouterClient", FakeDoctorClient)
    assert cli.main(["doctor", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "target local" in out
    assert "network" in out

    monkeypatch.setattr(cli, "OpenRouterClient", FailingDoctorClient)
    assert cli.main(["doctor", "--config", str(config)]) == 1
    assert "fail" in capsys.readouterr().out
