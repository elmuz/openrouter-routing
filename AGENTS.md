# AGENTS.md

## What this project is

`openrouter-routing` measures the token mix (`f`, `R`, `fw`) of pi sessions and
ranks OpenRouter endpoints by the real cost of that mix. It also inspects and
rewrites OpenRouter presets.

The core invariant is:

```text
C_eff = (1-f)*p_in + f*p_cr + R*p_out + fw*p_cw
```

Do not replace this with OpenRouter's `sort: price`; that ignores cache-read
pricing, which dominates agentic workloads. See `docs/how-it-works.md` and
`.agents/skills/openrouter-cost-routing/references/openrouter-billing.md`.

## Layout

- `src/openrouter_routing/` — importable package.
- `measure.py`, `cheapest.py`, `preset.py` — compatibility wrappers only.
- `tests/` — pytest suite. Network is mocked/injected; keep it that way.
- `docs/` — user documentation; `README.md` stays a short overview.
- `docs/assets/` — images referenced by the documentation.
- `examples/mix.json` — sample output, not a runtime input.
- `systemd/` — reserved for the **opt-in** automation phase; nothing is
  installed or enabled by default.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run openrouter-routing measure --days 7 --by project
uv run openrouter-routing cheapest deepseek/deepseek-v4-flash --mix examples/mix.json
uv run openrouter-routing preset plan ds4-flash --mix examples/mix.json
```

## Engineering rules

1. **No network in tests.** Inject `OpenRouterClient` or its opener; use fixture
   sessions. The default `uv run pytest` must work offline.
2. **Read-only by default.** `plan`, `show`, and `cheapest` must not mutate
   OpenRouter. Any write requires an explicit `--apply` (or a dedicated future
   `refresh --apply`).
3. **Preserve unknown preset fields.** A preset update is a full-config POST;
   copy the current config and replace only fields this tool owns.
4. **Skip no-op writes.** Do not create a preset version when `provider.order`
   is unchanged (`--force` may override).
5. **Keep the legacy entry points working.** Root scripts must keep delegating
   to the package CLI.
6. **Zero runtime dependencies.** Use the standard library for runtime code.
   Dev-only dependencies belong in `[dependency-groups]`.
7. **XDG paths.** Do not hard-code new config/state paths; use
   `openrouter_routing.paths`.
8. **Pi-only today, not forever.** Session parsing lives in `sessions.py`; the
   routing/pricing layer must not depend on pi-specific log details.
9. **Never commit secrets.** `OPENROUTER_API_KEY` and `~/.pi/agent/auth.json`
   are runtime inputs only.
10. **Document formula changes.** If you touch pricing semantics, update
    `docs/how-it-works.md` (and the `README.md` summary if it changes), the
    skill reference, and tests in the same change.
11. **Keep the README short.** Detailed usage belongs in `docs/`; link it from
    the README instead of growing the README.

## Auth

`OPENROUTER_API_KEY` first, then `~/.pi/agent/auth.json` (`openrouter` entry).
Endpoint stats (`throughput_last_30m`, `latency_last_30m`) require an
authenticated call; unauthenticated calls return null for them.

## Current project phase

Phase 0–3 are implemented: packaging, git, tests, XDG config, the
`measure` / `cheapest` / `preset` CLI, full analysis filters, `--anchor now`,
`--last-sessions`, `--top-projects`, opt-in `--cost-model`, TSV output, and
session-source/robustness work. See `PLAN.md` for the remaining roadmap:
refresh workflow, local pi `models.json` target, opt-in systemd installation,
and cost verification.

`measure` is offline unless `--cost-model` is passed; that is the one opt-in
network path and it must stay read-only.
