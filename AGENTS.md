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
- `src/openrouter_routing/refresh.py` — refresh orchestration, window, state.
- `src/openrouter_routing/targets.py` — preset and local pi `models.json` backends.
- `src/openrouter_routing/verify.py` — generation-cost verification and divergence flags.
- `src/openrouter_routing/state.py` — XDG state file and refresh lock.
- `src/openrouter_routing/fsutil.py` — atomic writes and timestamped backups.
- `src/openrouter_routing/automation.py` — opt-in systemd user unit installer.
- `src/openrouter_routing/doctor.py` — config/session/auth/network/target checks.
- `measure.py`, `cheapest.py`, `preset.py` — compatibility wrappers only.
- `tests/` — pytest suite. Network is mocked/injected; keep it that way.
- `docs/` — user documentation; `README.md` stays a short overview.
- `docs/assets/` — images referenced by the documentation.
- `examples/mix.json` — sample output, not a runtime input.
- `systemd/` — canonical **opt-in** systemd user unit templates. Nothing is
  installed or enabled by default; only `install-systemd --enable` writes and
  enables them.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run openrouter-routing measure --days 7 --by project
uv run openrouter-routing cheapest deepseek/deepseek-v4-flash --mix examples/mix.json
uv run openrouter-routing preset plan ds4-flash --mix examples/mix.json
uv run openrouter-routing refresh --target ds4-flash
uv run openrouter-routing verify --last 20
uv run openrouter-routing install-systemd --mode daily   # preview only
uv run openrouter-routing doctor
```

## Engineering rules

1. **No network in tests.** Inject `OpenRouterClient` or its opener; use fixture
   sessions. The default `uv run pytest` must work offline.
2. **Read-only by default.** `plan`, `show`, `cheapest`, and `verify` must not
   mutate OpenRouter. Any write requires an explicit `--apply` (today only
   `refresh` and preset apply paths write).
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
12. **Plan all targets before writing any.** A network/config/ranking failure
    during `refresh` must leave every target untouched.
13. **Local target writes are atomic.** Validate JSON, take a timestamped
    backup, write a same-directory temp file, then `os.replace`.
14. **`refresh` is dry-run by default.** `--apply` is the only write path;
    unchanged targets are skipped so repeated runs do not create version noise.
15. **Systemd automation stays opt-in.** Preview is the default; only
    `install-systemd --enable` may write units or run `systemctl`. Never enable
    a timer from `uv sync`, `config init`, or another ordinary command.

## Auth

`OPENROUTER_API_KEY` first, then `~/.pi/agent/auth.json` (`openrouter` entry).
Endpoint stats (`throughput_last_30m`, `latency_last_30m`) require an
authenticated call; unauthenticated calls return null for them. `verify` and
preset writes require credentials.

## Current project phase

Phase 0–6 are implemented: packaging, git, tests, XDG config, the
`measure` / `cheapest` / `preset` / `verify` CLI, full analysis filters,
`--anchor now`, `--last-sessions`, `--top-projects`, opt-in `--cost-model`, TSV
output, session-source/robustness work, the `refresh` workflow with preset and
local pi `models.json` targets, `order`/`preferred` routing modes, XDG state and
locking, `--log-file`, `doctor`, the opt-in systemd user timer installer, and
generation-record cost verification with `cheapest --compare-current`. See
`PLAN.md` for the remaining release steps (manual `v0.2.0` tag and optional
PyPI publication).

`measure` is offline unless `--cost-model` is passed; `cheapest`, `preset`,
`refresh`, `verify`, and `doctor` normally talk to OpenRouter. `refresh` and the
preset commands remain read-only unless `--apply` is passed. `install-systemd`
is a preview unless `--enable` is passed. `verify` is read-only but always uses
the network.
