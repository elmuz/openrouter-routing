# Opt-in systemd automation

`openrouter-routing` does not install or enable anything automatically. `uv
sync`, `config init`, `refresh`, and `doctor` never create a systemd unit or
timer. The timer is an explicit, reversible opt-in.

## Preview

The installer is a dry run by default; it renders the units and the commands it
would run but writes nothing:

```bash
openrouter-routing install-systemd --mode daily
openrouter-routing install-systemd --mode daily --print   # same preview
```

## Install and enable

`--enable` is the opt-in action. It writes the two user units, runs
`systemctl --user daemon-reload`, and enables and starts the timer:

```bash
openrouter-routing install-systemd --mode daily --enable
```

Units go to `${XDG_CONFIG_HOME:-~/.config}/systemd/user/`:

```text
openrouter-routing.service
openrouter-routing.timer
```

The timer runs this command, with the current config path captured explicitly:

```text
openrouter-routing refresh --apply --config <absolute-config-path>
```

The default executable is `openrouter-routing` from `PATH`. In a source
checkout it falls back to the repository virtualenv binary or `python -m
openrouter_routing`. Override it with `--binary PATH`
(`--binary` also accepts a command plus arguments); override the unit directory
with `--unit-dir DIR`.

Existing units are never overwritten unless `--force` is passed. Re-run with
`--force --enable` after changing `--binary`, `--config`, or the templates.

## Timer behavior

| Setting | Effect |
|---|---|
| `OnStartupSec=2min` | a user-manager start (normally login) schedules a refresh about two minutes later |
| `OnUnitActiveSec=1d` | long-running sessions refresh once a day after the previous run |
| `Persistent=true` | a missed run is caught up when the timer becomes active again |
| `RandomizedDelaySec=15min` | spreads runs to avoid a thundering herd |
| `WantedBy=timers.target` | the timer is part of the standard user timer target |

The service is `Type=oneshot` and writes to the user journal. A refresh plans
every target before writing any of them, and the refresh lock prevents a manual
run from overlapping the timer.

`refresh --apply` is used because a timer that only printed a plan would not do
anything. The underlying routing writes still preserve unknown fields, skip
no-op targets, and create preset versions only when the routing changes.

## Journald and credentials

`StandardOutput=journal` and `StandardError=journal` are set, so:

```bash
systemctl --user status openrouter-routing.timer openrouter-routing.service
systemctl --user list-timers openrouter-routing.timer
journalctl --user -u openrouter-routing.service --since today
journalctl --user -u openrouter-routing.service -f
```

The user service does not inherit your interactive shell environment. The
installer captures the config path, but `OPENROUTER_API_KEY` is not inherited.
Use the `openrouter` entry in `~/.pi/agent/auth.json`, or expose the variable to
the user manager explicitly (for example with a private
`~/.config/environment.d/*.conf` file, never a committed secret). Without a key,
endpoint performance statistics are null and the rank filters may not behave as
intended.

## Running while logged out

A user manager normally stops at logout. To let the timer run while you are
logged out:

```bash
loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger     # Linger=yes
```

Disable it again with `loginctl disable-linger "$USER"`. With lingering enabled,
the user manager starts at boot and `OnStartupSec` schedules the same
login-triggered refresh; `OnUnitActiveSec=1d` continues the daily cadence.

## Disable and remove

```bash
openrouter-routing install-systemd --disable           # disable --now, keep files
openrouter-routing install-systemd --remove            # disable, delete files, daemon-reload
openrouter-routing install-systemd --remove --print    # preview both commands
```

`--remove` implies `--disable`. It is the reversible uninstall path: after it,
enabling requires a new explicit `install-systemd --enable`.
