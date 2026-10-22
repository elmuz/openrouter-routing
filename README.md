<p align="center">
  <img src="docs/assets/logo.jpg" alt="openrouter-routing logo" width="160">
</p>

# openrouter-routing

[![CI](https://github.com/elmuz/openrouter-routing/actions/workflows/ci.yml/badge.svg)](https://github.com/elmuz/openrouter-routing/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

Cache-aware OpenRouter provider routing for [pi](https://pi.dev) sessions.

OpenRouter bills prompt, completion, cache-read, and cache-write tokens
separately. In agentic sessions most input tokens are cache hits, so the
cache-read price dominates the bill — and OpenRouter's `sort: price`, which
only looks at prompt and completion prices, picks the wrong endpoint.

`openrouter-routing` measures *your* token mix from pi's session logs, ranks
every endpoint of a model by the real cost of that mix, and emits the
`provider.order` that minimizes it:

```text
C_eff = (1-f)·p_in + f·p_cr + R·p_out + fw·p_cw
```

where `f` is the cache-hit share of input tokens, `R` the output/input ratio,
and `fw` the cache-write ratio. See [How it works](docs/how-it-works.md) for the
reasoning and measured numbers.

## Install

```bash
git clone git@github.com:elmuz/openrouter-routing.git
cd openrouter-routing
uv sync
```

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The runtime has no
third-party dependencies.

## Quick start

```bash
# 1. Measure a recent working window
uv run openrouter-routing measure --days 7 --by project

# 2. Save the mix
uv run openrouter-routing measure --days 7 --json > /tmp/mix.json

# 3. Rank the endpoints of a model or preset alias
uv run openrouter-routing cheapest '~deepseek/deepseek-flash-latest' --mix /tmp/mix.json

# 4. Plan a preset update (read-only)
uv run openrouter-routing preset plan ds4-flash --mix /tmp/mix.json --top 4

# 5. Apply it explicitly
uv run openrouter-routing preset plan ds4-flash --mix /tmp/mix.json --top 4 --apply

# 6. Or refresh configured targets (dry run by default)
uv run openrouter-routing refresh
uv run openrouter-routing refresh --apply
uv run openrouter-routing verify --last 20
uv run openrouter-routing doctor

# Optional: opt-in daily systemd user timer (preview first)
uv run openrouter-routing install-systemd --mode daily
uv run openrouter-routing install-systemd --mode daily --enable
```

`measure`, `cheapest`, `preset show/plan`, `refresh`, and `verify` are
read-only; writes require `--apply`. `measure` is fully offline unless
`--cost-model` is passed, while `verify` always uses the network. The legacy
`./measure.py`, `./cheapest.py`, and `./preset.py` wrappers still work.

## Documentation

| Document | Contents |
|---|---|
| [How it works](docs/how-it-works.md) | Token mix, cost formula, measured numbers, limitations |
| [Measure](docs/measure.md) | Time windows, filters, summaries, output formats |
| [Routing](docs/routing.md) | Endpoint ranking, `--provider-json`, routing semantics |
| [Presets](docs/presets.md) | `preset show`, `plan`, `rollback` |
| [Refresh](docs/refresh.md) | Daily refresh, targets, routing modes, state and locks |
| [Verify](docs/verify.md) | Compare predicted cost with real OpenRouter generation records |
| [Automation](docs/automation.md) | Opt-in systemd user timer: preview, enable, linger, uninstall |
| [Configuration](docs/configuration.md) | XDG paths, `config.toml`, environment variables |
| [Development](docs/development.md) | Setup, tests, repository layout, roadmap |

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Tests are offline by default. See `AGENTS.md` for repository invariants and
`PLAN.md` for the roadmap.

## License

[MIT](LICENSE)
