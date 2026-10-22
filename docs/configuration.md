# Configuration

`openrouter-routing` follows XDG paths:

```text
config: ${XDG_CONFIG_HOME:-~/.config}/openrouter-routing/config.toml
cache:  ${XDG_CACHE_HOME:-~/.cache}/openrouter-routing/
state:  ${XDG_STATE_HOME:-~/.local/state}/openrouter-routing/
```

```bash
openrouter-routing config path
openrouter-routing config init       # writes a starter config
openrouter-routing config show       # effective values as JSON
```

Environment overrides: `OPENROUTER_ROUTING_CONFIG`, `PI_AGENT_DIR`,
`OPENROUTER_API_KEY`, and the standard `XDG_*` variables.

Authentication uses `OPENROUTER_API_KEY` first, then the `openrouter` entry in
`~/.pi/agent/auth.json`. Endpoint stats (`throughput_last_30m`,
`latency_last_30m`) require an authenticated call; unauthenticated calls return
null for them.

## Example config

```toml
[measure]
days = 7
# anchor = "latest"             # or "now" for wall-clock windows
# projects = ["~/code", "~/projects"]
# models = ["@preset/ds4-flash"]

[refresh]
days = 7
anchor = "now"                 # refresh defaults to wall clock
routing_mode = "order"         # order | preferred
lock_timeout = 0
# log_file = "~/.local/state/openrouter-routing/refresh.log"

[rank]
top = 4
healthy_only = true
require_cache = true
min_tps = 40
max_ttft = 6
tps_pct = "p90"
ttft_pct = "p90"
prefer = true
max_price_from = 0

[[target]]
name = "ds4-flash"
kind = "preset"              # preset | pi-model
preset = "ds4-flash"
model = "~deepseek/deepseek-flash-latest"
# routing_mode = "preferred"  # optional per-target override
# projects = ["~/code"]       # optional per-target mix filters
# models = ["@preset/ds4-flash"]

[[target]]
name = "local-ds4"
kind = "pi-model"
pi_model_id = "@preset/ds4-flash"
pi_provider = "openrouter"
model = "~deepseek/deepseek-flash-latest"
```

Command-line flags override config values. `[measure]` defaults apply only when
no CLI time flag is given. `[refresh]` defaults apply to the `refresh` command;
see [Refresh](refresh.md) for target filters, routing modes, XDG state, and the
lock file.

`kind = "pi-model"` patches `compat.openRouterRouting` in
`~/.pi/agent/models.json` atomically (or the configured `pi_models_json`) and
keeps a timestamped backup. The preset target remains the recommended default
until request-level routing precedence over a preset has been verified with a
controlled live request.

## Automation

No systemd unit, timer, or login hook is installed or enabled by default. The
installer is a dry run unless `--enable` is passed:

```bash
openrouter-routing install-systemd --mode daily            # preview
openrouter-routing install-systemd --mode daily --enable   # write + enable
```

`--enable` installs
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/openrouter-routing.{service,timer}`,
reloads the user manager, and enables the timer. The timer refreshes after login
and once a day while the user manager stays alive, with `Persistent=true` and a
randomized delay. `--disable` turns it off without deleting files; `--remove` is
the reversible uninstall. See [Automation](automation.md) for journald, linger,
credentials, and uninstall details.

Until you opt in, run `openrouter-routing refresh --apply` manually or from your
own wrapper. `refresh --log-file` is available for wrappers that cannot use
journald.
