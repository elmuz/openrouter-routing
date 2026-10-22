# openrouter-routing

Cache-aware OpenRouter provider routing for [pi](https://pi.dev) sessions.

OpenRouter bills four token classes: prompt, completion, cache read, and cache
write. For agentic work most input tokens are cache hits, so the cache-read
price is the price. OpenRouter's `sort: price` only looks at prompt+completion
and therefore picks the wrong endpoint. This project measures *your* token mix
from pi's session logs, scores every endpoint with that mix, and emits the
`provider.order` that minimizes the real cost.

The repository started as three standalone scripts. They still work, but the
supported interface is now the `openrouter-routing` CLI.

## Quick start

```bash
git clone git@github.com:elmuz/openrouter-routing.git
cd openrouter-routing
uv sync

# 1. What does a recent working window look like?
uv run openrouter-routing measure --days 7 --by project

# 2. Save the mix
uv run openrouter-routing measure --days 7 --json > /tmp/mix.json

# 3. Rank the endpoints of one model / preset alias
uv run openrouter-routing cheapest '~deepseek/deepseek-flash-latest' --mix /tmp/mix.json

# 4. Plan a preset update (read-only)
uv run openrouter-routing preset plan ds4-flash --mix /tmp/mix.json --top 4

# 5. Apply it explicitly
uv run openrouter-routing preset plan ds4-flash --mix /tmp/mix.json --top 4 --apply
```

Legacy entry points remain available as thin wrappers:

```bash
./measure.py --days 7
./cheapest.py deepseek/deepseek-v4-flash --mix /tmp/mix.json
./preset.py show ds4-flash --versions 5
```

## The formula

OpenRouter bills per request as:

```text
cost = (n_in - n_cr)·p_in + n_cr·p_cr + n_out·p_out + n_cw·p_cw
```

Aggregate over a session (sum tokens, never average per-request ratios) and
normalize on total input tokens:

```text
f  = Σn_cr / Σ(n_in + n_cr)        cache-hit share of input tokens
R  = Σn_out / Σ(n_in + n_cr)       output tokens per input token
fw = Σn_cw / Σ(n_in + n_cr)        cache-write tokens per input token

C_eff = (1-f)·p_in + f·p_cr + R·p_out + fw·p_cw
```

Providers that cannot cache are scored with `f = fw = 0` and `p_cr = p_in`:
for them every input token is a miss.

Measured on this machine (8,249 recorded requests):

```text
fresh input   86,971,668      f  = 0.9206
cache read  1,007,824,815      R  = 0.00554
output         6,067,372      fw = 0
```

The weights per 1M input tokens are therefore roughly **8% p_in + 92% p_cr +
0.55% p_out**. Cache-read price dominates. A no-cache endpoint needs about
`1/(1-f)` ≈ 12.6× cheaper input to compete at the global mix, and more in warm
sessions.

`R` matters more for chat-style workloads (short prompts, few cache hits); keep
one mix per workload class. That is why `measure` supports model, project, and
time filters.

## Filters

`measure` can select the sessions and records that represent the workflow you
care about:

```bash
# Last 7 days only
openrouter-routing measure --days 7

# A project directory tree (matches the session cwd from the pi header)
openrouter-routing measure --days 7 --project ~/code/server-mcp

# Several projects, grouped by project
openrouter-routing measure --days 30 \
  --project ~/code --project ~/projects \
  --by project

# Requested model (pi's model id, e.g. @preset/ds4-flash)
openrouter-routing measure --days 30 --model '@preset/ds4-flash'

# Upstream model actually reported by OpenRouter
openrouter-routing measure --days 30 --routed-model 'deepseek/*'

# Exclude experiments/one-offs
openrouter-routing measure --days 30 --exclude-model ':free'
```

Time windows follow the old `measure.py` semantics: `--hours`/`--days` end at
the latest recorded event, which makes analysis reproducible. Config defaults
can supply a window when no CLI time flag is given.

JSON output adds a schema field, the resolved window, filters, and optional
groups while keeping the original `f`, `R`, `fw`, and token counters:

```json
{
  "schema": "openrouter-routing/mix/v1",
  "f": 0.9541,
  "R": 0.00578,
  "fw": 0.0,
  "requests": 4231
}
```

## Ranking endpoints

```bash
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json --json
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json --order
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json --provider-json
```

Useful filters:

```bash
--healthy-only                 # status == 0 only
--require-cache                # drop endpoints without cache pricing
--quant fp8,fp16               # restrict quantization
--min-tps 40 --tps-pct p90     # throughput floor
--max-ttft 6 --ttft-pct p90    # time-to-first-token floor
--prefer                       # also emit OpenRouter's soft preferred_* cutoffs
--max-price-from N             # size max_price from the Nth ranked endpoint
```

`--provider-json` prints a ready routing block:

```json
{
  "provider": {
    "order": ["deepinfra/fp8", "sail-research/fp4", "cloudflare"],
    "allow_fallbacks": true,
    "max_price": { "prompt": 0.101, "completion": 0.15 },
    "preferred_min_throughput": { "p90": 40 },
    "preferred_max_latency": { "p90": 6 }
  }
}
```

Semantics:

- `order` is tried first and in sequence. Keep `allow_fallbacks: true` so an
  outage degrades to the next endpoint instead of failing.
- `max_price` is a drift guardrail, not an optimizer. It compares plain
  prompt/completion prices, so size it from a later endpoint with
  `--max-price-from`.
- `preferred_*` is a soft preference: endpoints that miss it are demoted, not
  dropped. `--min-tps` / `--max-ttft` are hard filters in this tool.
- OpenRouter's provider **sticky routing is disabled when `provider.order` is
  set**. The order still keeps you on the chosen endpoint; the tradeoff is worth
  documenting per preset and may become a selectable routing mode later.

## Presets

There is no `PUT`/`PATCH`/`DELETE` for presets in the public API. Updating one
means re-POSTing a full config to:

```text
POST /api/v1/presets/<slug>/chat/completions
```

Only fields overlapping the preset config are persisted; `messages` are
ignored, so no tokens are spent. Every write creates a new version.

```bash
openrouter-routing preset show ds4-flash --versions 5
openrouter-routing preset plan ds4-flash --mix mix.json --top 4
openrouter-routing preset plan ds4-flash --mix mix.json --top 4 --apply
openrouter-routing preset rollback ds4-flash --version 8 --apply
```

`plan` re-reads the current config, preserves fields it does not own (`sort`,
`ignore`, `quantizations`, `allow_fallbacks`, and unknown keys), and replaces
only `provider.order` plus the filters you request. It resolves
`~author/model-latest` aliases because those aliases expose no endpoints.

A no-op plan does **not** create a new version unless `--force` is passed.

Authentication uses `OPENROUTER_API_KEY`, falling back to the OpenRouter entry
in `~/.pi/agent/auth.json`. `show` and `plan` without `--apply` are read-only.

## Configuration

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

Example:

```toml
[measure]
days = 7
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

`targets` are scaffolding for the refresh workflow; the Phase 0–2 CLI exposes
them through `config show`, while end-to-end `refresh --apply` and the local
`pi-model` target land in a later phase.

## Automation

No systemd unit, timer, or login hook is installed or enabled by default. When
the opt-in automation phase lands it will use a daily timer
(`Persistent=true`) and an explicit install command. Until then, run the CLI
manually or from your own wrapper.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The runtime has no third-party dependencies. Tests use fixture sessions and
injected HTTP clients, so the default test run never touches the network.

See `AGENTS.md` for repository invariants, `PLAN.md` for the detailed phase plan
and roadmap, and `.agents/skills/openrouter-cost-routing/` for the agent-facing
OpenRouter billing/routing references.

## Notes and limits

- pi records the requested model, not the upstream provider. It does record
  `responseModel` and `responseId`.
- Cached-token counts depend on pi/provider reporting. Cache TTL, minimum
  cacheable prompt size, truncation, and cache-write pricing can make the
  realized cost differ from the model; validate against
  `GET /api/v1/generation?id=<responseId>` when in doubt.
- `max_price` has no cache field in OpenRouter's API; that is why the optimal
  cache-aware routing has to be computed offline and expressed as `order`.
- The project targets OpenRouter. The session loader is isolated so another
  harness can be added later without changing the pricing logic.
