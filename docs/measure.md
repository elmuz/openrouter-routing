# Measure

`measure` aggregates pi session usage into the `f`, `R`, and `fw` mix. It reads
session JSONL files and is fully offline unless `--cost-model` is passed.

```bash
openrouter-routing measure --days 7 --by project
openrouter-routing measure --days 7 --json > mix.json
openrouter-routing measure path/to/session.jsonl   # explicit files
```

By default it reads the `[paths] sessions_glob` from the config
(`~/.pi/agent/sessions/**/*.jsonl`). Sessions can also be passed as positional
paths.

## Time windows

```bash
# Last 7 days, ending at the latest recorded event (reproducible)
openrouter-routing measure --days 7

# Last 7 days from wall clock (useful for scheduled refreshes)
openrouter-routing measure --days 7 --anchor now

# Only sessions, not a calendar window: the 3 most recent sessions
openrouter-routing measure --last-sessions 3

# Explicit bounds; both --since and --until are inclusive
openrouter-routing measure \
  --since 2026-10-01T00:00:00Z --until 2026-10-08T00:00:00Z
```

Relative windows (`--hours`, `--days`) end at the latest record by default. That
keeps ad-hoc analysis reproducible. `--anchor now` uses wall-clock time instead,
which is what the future `refresh` workflow needs.

`--last-sessions N` selects whole sessions by their latest record and is applied
*before* the time window, so `--last-sessions 3 --days 2` means "the last three
sessions, then the last two days of those sessions". An explicit `--since` wins
over a relative window.

The text output always prints the resolved absolute bounds, including for an
explicit `--since`:

```text
window   : 2026-10-01 10:00:02 .. 2026-10-08 18:22:11  (explicit bounds (inclusive))
```

## Project, model, and provider filters

```bash
# A project directory tree (matches the session cwd from the pi header)
openrouter-routing measure --days 7 --project ~/code/server-mcp

# Several projects, grouped by project
openrouter-routing measure --days 30 \
  --project ~/code --project ~/projects \
  --by project

# Exclude a project tree (repeatable)
openrouter-routing measure --days 30 --exclude-project ~/code/scratch

# Requested model (pi's model id, e.g. @preset/ds4-flash)
openrouter-routing measure --days 30 --model '@preset/ds4-flash'

# Upstream model actually reported by OpenRouter
openrouter-routing measure --days 30 --routed-model 'deepseek/*'

# Exclude experiments/one-offs
openrouter-routing measure --days 30 --exclude-model ':free'

# Filter by the pi provider field
openrouter-routing measure --days 30 --provider openrouter
```

Everything except the time filters is repeatable and supports substrings and
globs. Project roots are recursive; using `/` as a project root is rejected
because it would silently select every session on the machine.

`--by` accepts `project`, `model`, `routed-model`, and `provider-model`. Without
`--by`, groups are shown per `provider/model` when more than one is present.

## Project and cost summaries

```bash
# The 3 projects with the most input tokens
openrouter-routing measure --days 30 --top-projects 3

# Estimated USD cost per group, using a model's cheapest endpoint
openrouter-routing measure --days 30 --by project \
  --cost-model '~deepseek/deepseek-flash-latest'
```

`--top-projects` prints requests, token totals, `f`, `R`, and the per-1M cost
weights (`miss%`, `hit%`, `out%`, `cw%`).

`--cost-model` is the only `measure` option that touches the network. It
resolves the model, ranks its endpoints with the configured `[rank]` filters
(see [Routing](routing.md)), and uses the cheapest endpoint to estimate the
overall and per-group cost. Without that flag `measure` is fully offline.

## Output formats

```bash
openrouter-routing measure --days 7 --json
openrouter-routing measure --days 7 --format json
openrouter-routing measure --days 7 --format tsv --by project
openrouter-routing measure --days 7 --quiet    # suppress tips on stderr
openrouter-routing measure --days 7 --verbose  # config/session diagnostics
```

`--json` is an alias for `--format json`. JSON adds a schema field, the resolved
window, filters, deterministic group ordering, optional `top_projects`, and
optional `cost_model` metadata while keeping the original `f`, `R`, `fw`, and
token counters:

```json
{
  "schema": "openrouter-routing/mix/v1",
  "window": { "anchor": "latest", "days": 7, "relative": true },
  "f": 0.9541,
  "R": 0.00578,
  "fw": 0.0,
  "requests": 4231
}
```

Results go to stdout; tips, warnings, and verbose diagnostics go to stderr, so
`--format json` stays pipeable. The `tsv` format emits one stable header and one
row each for `overall`, each `--by` group, and each `--top-projects` entry.

## Saved mix

A saved mix JSON can be reused with `cheapest` and `preset plan`:

```bash
openrouter-routing measure --days 7 --json > mix.json
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json
```

See the sample in [`examples/mix.json`](../examples/mix.json).
