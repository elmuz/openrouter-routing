# Documentation

`openrouter-routing` measures the token mix of pi sessions and ranks OpenRouter
endpoints by the real cost of that mix.

Start with [How it works](how-it-works.md) if you want the reasoning, or go
straight to the command reference.

| Document | Contents |
|---|---|
| [How it works](how-it-works.md) | Token mix, `C_eff` formula, measured numbers, limitations |
| [Measure](measure.md) | `measure`: time windows, filters, summaries, output formats |
| [Routing](routing.md) | `cheapest`: endpoint ranking, `--provider-json`, routing semantics |
| [Presets](presets.md) | `preset show`, `preset plan`, `preset rollback` |
| [Configuration](configuration.md) | XDG paths, `config.toml`, environment variables, automation |
| [Development](development.md) | Setup, tests, repository layout, roadmap |

For agent-facing OpenRouter billing references, see
[`.agents/skills/openrouter-cost-routing/`](../.agents/skills/openrouter-cost-routing/).
