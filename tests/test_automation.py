from __future__ import annotations

from pathlib import Path

import pytest

from openrouter_routing import automation, cli
from openrouter_routing.errors import ConfigError

ROOT = Path(__file__).parents[1]


def fake_binary(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "openrouter-routing"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def write_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        '[[target]]\nname = "local"\nkind = "preset"\npreset = "p"\nmodel = "vendor/model"\n',
        encoding="utf-8",
    )
    return config


def test_tracked_templates_are_canonical() -> None:
    contents = automation.render_units(("openrouter-routing",))
    service_template = (ROOT / "systemd" / automation.SERVICE_NAME).read_text(encoding="utf-8")
    timer_template = (ROOT / "systemd" / automation.TIMER_NAME).read_text(encoding="utf-8")
    assert contents.service == service_template.replace("@EXEC_START@", "openrouter-routing")
    assert contents.timer == timer_template


def test_render_units_documents_semantics() -> None:
    contents = automation.render_units(("openrouter-routing", "refresh", "--apply"))
    assert "ExecStart=openrouter-routing refresh --apply" in contents.service
    assert "OnStartupSec=2min" in contents.timer
    assert "OnUnitActiveSec=1d" in contents.timer
    assert "Persistent=true" in contents.timer
    assert "RandomizedDelaySec=15min" in contents.timer
    assert "WantedBy=timers.target" in contents.timer
    assert "StandardOutput=journal" in contents.service


def test_exec_start_quotes_paths_with_spaces(tmp_path) -> None:
    spaced = tmp_path / "OpenRouter Tools"
    spaced.mkdir()
    spaced_binary = spaced / "openrouter-routing"
    spaced_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    spaced_binary.chmod(0o755)
    config = tmp_path / "my config.toml"
    command = automation.resolve_exec_start(str(spaced_binary), config)
    contents = automation.render_units(command)
    expected = f'ExecStart="{spaced_binary}" refresh --apply --config "{config}"'
    assert expected in contents.service


def test_resolve_unit_dir_uses_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert automation.resolve_unit_dir() == tmp_path / "cfg" / "systemd" / "user"
    assert automation.resolve_unit_dir(tmp_path / "units") == tmp_path / "units"


def test_install_print_only_writes_nothing(tmp_path) -> None:
    binary = fake_binary(tmp_path)
    result = automation.install_systemd(
        config_path=tmp_path / "config.toml",
        unit_dir=tmp_path / "units",
        binary=str(binary),
        print_only=True,
    )
    assert result.wrote is False
    assert result.enabled is False
    assert result.commands == ()
    assert not (tmp_path / "units").exists()
    assert str(binary) in result.service_text


def test_install_writes_and_enables_with_injected_runner(tmp_path) -> None:
    binary = fake_binary(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(command):
        calls.append(tuple(command))
        return 0

    result = automation.install_systemd(
        config_path=tmp_path / "config.toml",
        unit_dir=tmp_path / "units",
        binary=str(binary),
        enable=True,
        runner=runner,
    )
    assert result.wrote is True
    assert result.enabled is True
    assert result.service_path.read_text(encoding="utf-8") == result.service_text
    assert result.timer_path.read_text(encoding="utf-8") == result.timer_text
    assert result.service_path.stat().st_mode & 0o777 == 0o644
    assert calls == [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", automation.TIMER_NAME),
    ]


def test_install_refuses_overwrite_without_force(tmp_path) -> None:
    binary = fake_binary(tmp_path)
    kwargs = {
        "config_path": tmp_path / "config.toml",
        "unit_dir": tmp_path / "units",
        "binary": str(binary),
    }
    automation.install_systemd(**kwargs)
    with pytest.raises(ConfigError, match="--force"):
        automation.install_systemd(**kwargs)
    replaced = automation.install_systemd(**kwargs, force=True)
    assert replaced.wrote is True


def test_install_reports_systemctl_failure(tmp_path) -> None:
    binary = fake_binary(tmp_path)

    def runner(command):
        return 7

    with pytest.raises(ConfigError, match="exit status 7"):
        automation.install_systemd(
            config_path=tmp_path / "config.toml",
            unit_dir=tmp_path / "units",
            binary=str(binary),
            enable=True,
            runner=runner,
        )


def test_uninstall_preview_then_remove(tmp_path) -> None:
    binary = fake_binary(tmp_path)
    automation.install_systemd(
        config_path=tmp_path / "config.toml",
        unit_dir=tmp_path / "units",
        binary=str(binary),
    )
    preview = automation.uninstall_systemd(unit_dir=tmp_path / "units", remove=True, print_only=True)
    assert preview.existing is True
    assert preview.commands == (
        ("systemctl", "--user", "disable", "--now", automation.TIMER_NAME),
        ("systemctl", "--user", "daemon-reload"),
    )
    assert preview.timer_path.exists()
    assert preview.removed is False

    calls: list[tuple[str, ...]] = []

    def runner(command):
        calls.append(tuple(command))
        return 0

    result = automation.uninstall_systemd(unit_dir=tmp_path / "units", remove=True, runner=runner)
    assert result.disabled is True
    assert result.removed is True
    assert calls == [
        ("systemctl", "--user", "disable", "--now", automation.TIMER_NAME),
        ("systemctl", "--user", "daemon-reload"),
    ]
    assert not result.service_path.exists()
    assert not result.timer_path.exists()


def test_uninstall_disable_keeps_unit_files(tmp_path) -> None:
    binary = fake_binary(tmp_path)
    automation.install_systemd(
        config_path=tmp_path / "config.toml",
        unit_dir=tmp_path / "units",
        binary=str(binary),
    )
    result = automation.uninstall_systemd(
        unit_dir=tmp_path / "units",
        runner=lambda command: 0,
    )
    assert result.disabled is True
    assert result.removed is False
    assert result.service_path.exists()
    assert result.timer_path.exists()


def test_uninstall_without_units_is_a_noop(tmp_path) -> None:
    result = automation.uninstall_systemd(unit_dir=tmp_path / "units")
    assert result.existing is False
    assert result.commands == ()


def test_cli_install_is_dry_run_by_default(tmp_path, monkeypatch, capsys) -> None:
    binary = fake_binary(tmp_path)
    config = write_config(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    def boom(command):  # pragma: no cover - must never run
        raise AssertionError("systemctl must not run in dry-run mode")

    monkeypatch.setattr(automation, "run_command", boom)
    assert cli.main(["install-systemd", "--config", str(config), "--binary", str(binary)]) == 0
    out = capsys.readouterr().out
    assert "ExecStart=" in out
    assert "OnUnitActiveSec=1d" in out
    assert "dry run" in out
    assert not (tmp_path / "cfg" / "systemd" / "user").exists()


def test_cli_install_enable_and_remove(tmp_path, monkeypatch, capsys) -> None:
    binary = fake_binary(tmp_path)
    config = write_config(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls: list[tuple[str, ...]] = []

    def runner(command):
        calls.append(tuple(command))
        return 0

    monkeypatch.setattr(automation, "run_command", runner)
    unit_dir = tmp_path / "cfg" / "systemd" / "user"

    assert cli.main(["install-systemd", "--config", str(config), "--binary", str(binary), "--enable"]) == 0
    out = capsys.readouterr().out
    assert "timer enabled" in out
    assert (unit_dir / automation.SERVICE_NAME).exists()
    assert (unit_dir / automation.TIMER_NAME).exists()
    assert calls == [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", automation.TIMER_NAME),
    ]

    calls.clear()
    assert cli.main(["install-systemd", "--disable"]) == 0
    assert "unit files kept" in capsys.readouterr().out
    assert calls == [("systemctl", "--user", "disable", "--now", automation.TIMER_NAME)]
    assert (unit_dir / automation.TIMER_NAME).exists()

    calls.clear()
    assert cli.main(["install-systemd", "--remove"]) == 0
    assert "removed" in capsys.readouterr().out
    assert calls == [
        ("systemctl", "--user", "disable", "--now", automation.TIMER_NAME),
        ("systemctl", "--user", "daemon-reload"),
    ]
    assert not (unit_dir / automation.SERVICE_NAME).exists()
    assert not (unit_dir / automation.TIMER_NAME).exists()


def test_cli_rejects_conflicting_flags(tmp_path, monkeypatch, capsys) -> None:
    binary = fake_binary(tmp_path)
    config = write_config(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert (
        cli.main(["install-systemd", "--config", str(config), "--binary", str(binary), "--enable", "--print"])
        == 1
    )
    assert "not both" in capsys.readouterr().err
    assert cli.main(["install-systemd", "--enable", "--disable"]) == 1
    assert "cannot be combined" in capsys.readouterr().err
