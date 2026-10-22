from __future__ import annotations

import pytest

from openrouter_routing.config import ConfigError, config_template, load_config


def test_missing_config_uses_defaults(tmp_path) -> None:
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.loaded is False
    assert cfg.paths.sessions_glob.name == "*.jsonl"
    assert cfg.rank.top is None
    assert cfg.targets == ()


def test_load_full_config(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[paths]
pi_agent_dir = "~/agent"

[measure]
days = 7
anchor = "now"
projects = ["~/code"]
models = ["@preset/main"]

[rank]
top = 3
healthy_only = true
require_cache = false
min_tps = 40
max_ttft = 6
tps_pct = "p75"
ttft_pct = "p90"
prefer = true
max_price_from = 1
quant = "fp8"

[[target]]
name = "main"
kind = "preset"
preset = "main"
model = "~vendor/model-latest"

[[target]]
name = "local"
kind = "pi-model"
pi_model_id = "@preset/main"
""",
        encoding="utf-8",
    )
    cfg = load_config(config)
    assert cfg.loaded is True
    assert cfg.measure.days == 7
    assert cfg.measure.anchor == "now"
    assert cfg.measure.projects == ("~/code",)
    assert cfg.measure.models == ("@preset/main",)
    assert cfg.rank.top == 3
    assert cfg.rank.healthy_only is True
    assert cfg.rank.require_cache is False
    assert cfg.rank.tps_pct == "p75"
    assert cfg.rank.max_price_from == 1
    assert cfg.rank.quant == "fp8"
    assert len(cfg.targets) == 2
    assert cfg.targets[0].model == "~vendor/model-latest"
    assert cfg.targets[1].pi_models_json == cfg.paths.pi_agent_dir / "models.json"
    assert cfg.to_dict()["targets"][0]["name"] == "main"


def test_refresh_and_target_filters(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[refresh]
 hours = 12
 anchor = "now"
 routing_mode = "preferred"
 lock_timeout = 2.5
 log_file = "refresh.log"

[[target]]
name = "local"
kind = "pi-model"
pi_model_id = "@preset/main"
pi_provider = "openrouter"
routing_mode = "order"
models = ["@preset/main"]
routed_models = ["vendor/*"]
exclude_models = [":free"]
projects = ["~/code"]
exclude_projects = ["~/code/scratch"]
""",
        encoding="utf-8",
    )
    cfg = load_config(config)
    assert cfg.refresh.hours == 12
    assert cfg.refresh.days is None
    assert cfg.refresh.anchor == "now"
    assert cfg.refresh.routing_mode == "preferred"
    assert cfg.refresh.lock_timeout == 2.5
    assert cfg.refresh.log_file == (tmp_path / "refresh.log").resolve()
    target = cfg.targets[0]
    assert target.routing_mode == "order"
    assert target.models == ("@preset/main",)
    assert target.routed_models == ("vendor/*",)
    assert target.exclude_models == (":free",)
    assert target.projects == ("~/code",)
    assert target.exclude_projects == ("~/code/scratch",)
    assert cfg.to_dict()["refresh"]["routing_mode"] == "preferred"
    assert cfg.to_dict()["targets"][0]["models"] == ["@preset/main"]


def test_refresh_defaults_and_validation(tmp_path) -> None:
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.refresh.days == 7
    assert cfg.refresh.anchor == "now"
    assert cfg.refresh.routing_mode == "order"

    config = tmp_path / "config.toml"
    config.write_text('[refresh]\nrouting_mode = "sticky"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
    config.write_text("[refresh]\nlock_timeout = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
    config.write_text('[[target]]\nrouting_mode = "nope"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
    config.write_text('[[target]]\nname = "same"\n[[target]]\nname = "same"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(config)


def test_relative_paths_are_relative_to_config(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[paths]\npi_agent_dir = "agent"\n', encoding="utf-8")
    cfg = load_config(config)
    assert cfg.paths.pi_agent_dir == (tmp_path / "agent").resolve()


def test_invalid_toml_and_values(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[rank\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
    config.write_text('[rank]\ntps_pct = "p42"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
    config.write_text('[rank]\nhealthy_only = "yes"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)
    config.write_text('[measure]\nanchor = "yesterday"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config)


def test_template_is_valid_toml(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(config_template(), encoding="utf-8")
    cfg = load_config(config)
    assert cfg.loaded is True
    assert cfg.rank.top == 4
    assert cfg.measure.anchor == "latest"
    assert cfg.refresh.days == 7
    assert cfg.refresh.anchor == "now"
