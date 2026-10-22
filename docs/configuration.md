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
kind = "preset"
preset = "ds4-flash"
model = "~deepseek/deepseek-flash-latest"
```

Command-line flags override config values. `[measure]` defaults apply only when
no CLI time flag is given.

`targets` are scaffolding for the refresh workflow; the CLI exposes them through
`config show`, while end-to-end `refresh --apply` and the local `pi-model` target
land in a later phase (see `PLAN.md`).

## Automation

No systemd unit, timer, or login hook is installed or enabled by default. When
the opt-in automation phase lands it will use a daily timer
(`Persistent=true`) and an explicit install command. Until then, run the CLI
manually or from your own wrapper.
