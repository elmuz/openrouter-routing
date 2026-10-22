# Refresh

`refresh` turns the analysis into a daily operation: measure a window from pi
sessions, rank endpoints with the configured `[rank]` filters, diff the result
against each configured target, and write only when `--apply` is passed.

```bash
openrouter-routing refresh                      # dry run, all targets
openrouter-routing refresh --target ds4-flash   # one target
openrouter-routing refresh --days 7 --apply     # explicit write
openrouter-routing refresh --compare --apply    # also estimate both modes
```

The command is read-only by default. `--dry-run` is accepted for symmetry and
cannot be combined with `--apply`.

## Targets

Targets live in `config.toml`. One block may be repeated per routing destination:

```toml
[refresh]
days = 7
anchor = "now"              # refresh defaults to wall clock
routing_mode = "order"      # order | preferred
lock_timeout = 0            # seconds to wait for a running refresh
# log_file = "~/.local/state/openrouter-routing/refresh.log"

[[target]]
name = "ds4-flash"
kind = "preset"             # preset | pi-model
preset = "ds4-flash"
model = "~deepseek/deepseek-flash-latest"
routing_mode = "order"      # optional per-target override
projects = ["~/code"]       # optional per-target mix filters
models = ["@preset/ds4-flash"]
routed_models = []
exclude_models = []
exclude_projects = []

[[target]]
name = "local-ds4"
kind = "pi-model"
pi_model_id = "@preset/ds4-flash"
pi_provider = "openrouter"
model = "~deepseek/deepseek-flash-latest"
```

Each target has its own mix. `projects`, `models`, `routed_models`,
`exclude_models`, and `exclude_projects` use the same matching rules as
`measure` (substrings/globs, recursive project roots). If a target has no
filters, it uses every record in the refresh window. Project filters are
validated so `/` cannot silently select the whole machine.

`kind = "pi-model"` requires both `model` (the OpenRouter model whose endpoints
are ranked) and `pi_model_id` (the entry in `models.json` to patch).

## Window

Refresh defaults to `days = 7`, `anchor = "now"` so a timer sees a stable
wall-clock window. An explicit `--since` wins over a relative window; `--hours`,
`--days`, `--until`, and `--anchor` override the `[refresh]` values. Unlike
`measure`, refresh defaults to `anchor = "now"`; ad-hoc analysis keeps
`anchor = "latest"`.

## Routing modes

### `order`

The cache-aware default. `provider.order` is set to the ranked endpoint list.
This is what the cost formula optimizes, but OpenRouter's automatic sticky
routing is disabled when a manual order is present. Refresh warns about this.

### `preferred`

Keeps sticky routing. Refresh removes `provider.order`, sets
`provider.sort = {by = "price"}`, preserves hard `ignore`/`quantizations`, and
turns `[rank] min_tps` / `max_ttft` into soft `preferred_min_throughput` /
`preferred_max_latency` floors. `rank.top` only applies to `order` mode.

`--compare` estimates the measured window under both modes. The preferred side
approximates `sort: price` by minimizing uncached input plus output price;
OpenRouter's exact sort tie-breaking is not public, so treat that number as an
estimate.

## Target backends

### Preset

The existing full-config POST path is wrapped behind a target backend. Unknown
preset fields are preserved, only routing-owned fields change, and a target
whose config is already correct is not posted. Every write creates a new preset
version. A preset with a `system_prompt` emits a warning because the write
endpoint has no documented way to carry it over.

### Local pi `models.json`

The `pi-model` backend patches only:

```text
providers.<pi_provider>.models[<pi_model_id>].compat.openRouterRouting
```

All other provider/model fields, unknown keys, and other models are preserved.
The new JSON is serialized and validated before replacement. The file is
replaced atomically, and the previous content is copied to a timestamped
backup (`models.json.bak.YYYYMMDDTHHMMSSZ`) first.

Request-level `provider` routing from pi's `compat.openRouterRouting` is
expected to take precedence over a preset. That precedence has not yet been
verified with a controlled live request, so the preset remains the recommended
default target.

## State, locks, and logs

A successful `--apply` run writes the last run to:

```text
${XDG_STATE_HOME:-~/.local/state}/openrouter-routing/state.json
```

The schema is `openrouter-routing/state/v1` and records the timestamp, window,
target, mix, routing mode, old/new order, routing blocks, whether anything was
applied, and any backup path. There is no history database.

A lock file (`refresh.lock`, in the same directory) prevents overlapping manual
and timer runs. A lock owned by a dead process is removed automatically.

`--log-file PATH` appends diagnostics; `[refresh] log_file` configures the same
path. Without it, output stays on stdout/stderr. The opt-in systemd service uses
journald; see [Automation](automation.md).

## Failure behavior

All targets are read and planned before any target is written. A network,
configuration, or ranking failure therefore leaves every target untouched.
The command returns non-zero on such failures. A target whose proposal is
unchanged is skipped without a write. State is still updated for a successful
apply run, including no-op runs, so the last run is observable.

After applying, use [`verify`](verify.md) over the following days to compare the
predicted cost with the real OpenRouter generation records.
