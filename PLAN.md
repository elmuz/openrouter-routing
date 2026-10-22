# Development plan and roadmap

This document records the plan followed so far and the detailed work remaining.
It is intentionally implementation-oriented: every phase lists deliverables,
acceptance criteria, and the decisions that should not be silently changed.

Repository: <https://github.com/elmuz/openrouter-routing>
Last updated: 2026-10-22.

## 1. Goals and constraints

Primary goal: keep OpenRouter provider routing optimized for the author's daily
pi workflow.

Constraints and preferences:

- The author is currently a pi-only user.
- OpenRouter is the only target; no other gateways/providers are in scope.
- Prefer a daily refresh, or a login-triggered refresh. Prices and endpoint
  economics do not change fast enough to justify frequent polling.
- Keep the runtime small and dependency-free where practical.
- Make configuration follow XDG conventions.
- Make history analysis filterable by model, time window, and project directory
  tree, because project-level token mixes differ materially.
- Keep tests fast, deterministic, and network-free by default.
- Any state-changing automation (especially systemd) must be **opt-in** and must
  never be enabled implicitly.

## 2. Current status

| Phase | Scope | Status |
|---|---|---|
| 0 | Repository bootstrap | Done |
| 1 | Package refactor, tests, CI | Done |
| 2 | XDG config, CLI, early filters | Done |
| 3 | Analysis polish and remaining filters | Done |
| 4 | `refresh` workflow and local pi target | Not started |
| 5 | Opt-in systemd automation | Not started |
| 6 | Cost verification and hardening | Not started |
| 7 | Other harness adapters | Deferred / low priority |

Phase 3 work is on top of `1338379 Initial Python package with XDG config and tests`.

Validation at the time of writing:

```text
uv run pytest                    65 passed
uv run ruff check .              clean
uv run ruff format --check .     clean
GitHub Actions                   Python 3.11, 3.12, 3.13, 3.14 green
```

Live smoke tests completed read-only:

```text
./measure.py --days 7
./cheapest.py '~deepseek/deepseek-flash-latest' --mix examples/mix.json
./preset.py show ds4-flash
./preset.py plan ds4-flash --mix examples/mix.json --top 4
```

No OpenRouter preset was modified during this work.

## 3. Architecture and invariants

### Cost model

```text
f  = ΣcacheRead / Σ(input + cacheRead)
R  = Σoutput    / Σ(input + cacheRead)
fw = ΣcacheWrite/ Σ(input + cacheRead)

C_eff = (1-f)*p_in + f*p_cr + R*p_out + fw*p_cw
```

Invariants:

1. Rank on `C_eff`, not on OpenRouter's `sort: price`.
2. A no-cache endpoint is scored with `f = fw = 0` and `p_cr = p_in`.
3. Aggregate token totals before computing ratios; never average per-request
   ratios.
4. Cache-read price dominates agentic workloads; do not hide that behind a
   generic "cheapest model" heuristic.
5. `provider.order` disables OpenRouter's automatic sticky cache routing. This
   tradeoff must be explicit in code, docs, and future routing modes.

### Package layout

```text
src/openrouter_routing/
├── api.py          OpenRouter HTTP client + alias resolution
├── cli.py          argparse CLI: measure / cheapest / preset / config
├── config.py       TOML config and dataclasses
├── errors.py       expected user-facing errors
├── mix.py          filtering, time windows, aggregation, grouping
├── paths.py        XDG and pi-agent paths
├── preset.py       preset config planning and field preservation
├── pricing.py      endpoint scoring and percentile helpers
├── routing.py      filtering, ranking, provider blocks
└── sessions.py     pi session JSONL parsing
```

Root `measure.py`, `cheapest.py`, and `preset.py` are compatibility wrappers
only. They add `src/` to `sys.path` and delegate to the package CLI.

### Engineering rules

- Runtime code uses the standard library; dev dependencies stay in
  `[dependency-groups]`.
- Network calls are injected/mocked in tests.
- Read-only commands never mutate presets or `models.json`.
- Writes require an explicit `--apply`.
- Preset updates preserve unknown fields.
- No-op preset updates do not create versions unless `--force`.
- The session layer is isolated so another harness can be added later.

## 4. Decisions made so far

| Decision | Choice | Rationale |
|---|---|---|
| Repository | `elmuz/openrouter-routing` | Descriptive, available, public |
| License | MIT | Simple public reuse |
| Layout | `src/` package | Testable import path, standard packaging |
| Python | `>=3.11` | `tomllib`, modern typing, current system is newer |
| Runtime deps | none | Scripts must remain easy to run |
| Dev deps | pytest, ruff | Fast local/CI checks |
| CLI | one `openrouter-routing` command | Avoids sibling-module imports and duplicated logic |
| Compatibility | root wrappers retained | Existing muscle memory/aliases keep working |
| Config | XDG TOML | Standard, predictable, environment-overridable |
| Config template | rank filters enabled, measure window commented | Safe useful defaults without changing ad-hoc analysis unexpectedly |
| Time windows | `--hours`/`--days` end at latest record | Preserves original `measure.py` semantics and reproducible analysis |
| Alias handling | resolve `~author/model-latest` via `/models` | The alias itself exposes no endpoints |
| Preset writes | full config POST, preserve unknown fields | API has no PATCH; partial bodies replace the config |
| No-op writes | skipped by default | Prevents daily version spam |
| Systemd | not installed, future opt-in only | User explicitly requested opt-in automation |
| Target default | preset target first | Proven path; local pi target needs precedence verification |

## 5. Phase 0 — Repository bootstrap (done)

Deliverables completed:

- `git init -b main`
- `pyproject.toml` with Hatchling build backend, console script, pytest/ruff config
- `uv.lock`
- `.gitignore` covering Python, uv, caches, local config, generated mix/provider
  files, secrets
- MIT `LICENSE`
- Rewritten `README.md`
- `AGENTS.md` with architecture, rules, and commands
- Project-local skill:
  - `.agents/skills/openrouter-cost-routing/SKILL.md`
  - `.agents/skills/openrouter-cost-routing/references/openrouter-billing.md`
- GitHub Actions CI for Python 3.11–3.14
- Initial signed commit
- Public GitHub repository and topics
- Current generated `mix.json` moved to `examples/mix.json`

Acceptance criteria met:

- `uv sync` works from a clean checkout.
- `uv run pytest` and `uv run ruff check .` pass.
- CI passes on all configured Python versions.
- No secrets or generated local state are tracked.

## 6. Phase 1 — Package refactor, tests, CI (done)

Deliverables completed:

- Moved all implementation into `src/openrouter_routing/`.
- Removed the `preset.py` → `cheapest.py` `sys.path` import hack.
- Introduced typed dataclasses/tables for session records and endpoint scores.
- Split pricing, routing, mix analysis, sessions, API, and preset logic.
- Added an injectable `OpenRouterClient`.
- Added alias resolution shared by both `cheapest` and `preset`.
- Kept `measure.py`, `cheapest.py`, `preset.py` as wrappers.
- Added byte-for-byte-ish backward compatibility for the old text/JSON output
  where practical.
- Added no-op detection for preset apply (`--force` to override).
- Added 44 tests covering:
  - timestamp parsing and session deduplication
  - session cwd/routed-model extraction
  - mix aggregation and empty input
  - project/model/routed-model filters
  - time windows and validation
  - grouping
  - endpoint pricing, cached vs no-cache scoring
  - percentile parsing
  - ranking, filtering, provider blocks
  - API client and alias resolution with fake HTTP
  - preset field preservation, alias handling, filters, preferences
  - CLI measure/config/cheapest smoke paths
  - config and XDG path resolution
  - starter config validity

Notable behavior changes from the original scripts:

- `cheapest` now resolves aliases such as
  `~deepseek/deepseek-flash-latest`.
- `preset plan --apply` skips no-op versions unless `--force`.
- `measure --json` includes a schema, window, filters, and optional groups while
  preserving the original `f`, `R`, `fw`, and token fields.

## 7. Phase 2 — XDG config and CLI (done)

Deliverables completed:

- `paths.py`:
  - `${XDG_CONFIG_HOME:-~/.config}/openrouter-routing/`
  - `${XDG_CACHE_HOME:-~/.cache}/openrouter-routing/`
  - `${XDG_STATE_HOME:-~/.local/state}/openrouter-routing/`
  - `PI_AGENT_DIR`, `OPENROUTER_ROUTING_CONFIG` overrides
- `config.py`:
  - dataclasses for paths, measure, rank, and targets
  - TOML loading with validation
  - config-relative path resolution
  - environment-aware defaults
  - starter template
- CLI commands:
  - `config path`
  - `config show`
  - `config init [--force]`
- Rank defaults are loaded from config when CLI flags are omitted.
- Measure defaults can come from `[measure]` when no time flag is passed.
- Session discovery defaults to the configured pi sessions glob.
- `cheapest` and `preset` use the same config-derived rank defaults.
- Targets are parsed and exposed through `config show`, but not yet consumed by
  a refresh workflow.

Early Phase 3 work landed here because it was cheap and improved usability:

- `measure --project` (repeatable, recursive cwd matching)
- `measure --routed-model`
- `measure --exclude-model`
- `measure --until`
- `measure --days`
- `measure --by project|model|routed-model|provider-model`
- session cwd extraction from the pi `type:"session"` header
- routed model extraction from `responseModel`

## 8. Phase 3 — Analysis polish (done)

### 3.1 Time-window controls

- [x] Expose `--anchor latest|now` on `measure`. The library already supports it;
      the CLI now uses it, and `measure.anchor` can set the default.
- [x] Add `--last-sessions N` for recent-session analysis.
- [x] Make `--until` exclusive/inclusive semantics explicit in help and tests
      (both bounds are inclusive).
- [x] Print the resolved absolute window in text output even for `--since`.
- [x] Add tests for `--anchor now` and `--last-sessions`.

Acceptance: a daily refresh can request "last 7 days from wall clock now" while
ad-hoc analysis keeps the reproducible latest-record behavior.

### 3.2 More filters and summaries

- [x] Add `--provider` filter (useful when other providers are ever present).
- [x] Add `--exclude-project`.
- [x] Add `--top-projects N` summary: requests, token totals, `f`, `R`, `C_eff`
      weights.
- [x] Add optional per-group estimated cost using a selected model/mix
      (`--cost-model`, the only network-touching `measure` option).
- [x] Make `--by` output deterministic and optionally `--json` only.
- [x] Validate that multiple `--project` roots cannot accidentally include `/`.

Acceptance: the author can answer "what does my daily work in this repo tree
look like?" without post-processing JSON.

### 3.3 Session robustness

- [x] Fallback cwd inference from the pi session directory name if a header is
      missing (best-effort; the directory encoding is lossy for hyphens).
- [x] Handle session header variants (`type: session` with whitespace, older
      versions). Header, usage, and id lookups accept common variants.
- [x] Add a `SessionSource` protocol so future harnesses are adapters rather
      than edits to `mix.py`/`cli.py`.
- [x] Add malformed/truncated JSONL fixture tests.
- [x] Add a fixture for duplicate IDs across files.

Acceptance: no crash on partially written session files; all skipped lines are
intentional and test-covered.

### 3.4 Output and UX

- [x] Add `--quiet`/`--verbose` and consistent stderr diagnostics.
- [x] Avoid recomputing `accumulate(group)` multiple times in JSON groups.
- [x] Add a compact `--format table|json|tsv`.
- [x] Document every filter with examples in `docs/measure.md`.

Implementation notes:

- `--last-sessions` is applied before the time window; an explicit `--since`
  wins over a relative window.
- `--cost-model` ranks endpoints with the configured `[rank]` filters and emits
  both overall and per-group `estimated_cost_usd`. It is opt-in because it is
  the only network call in `measure`.
- `--format tsv` writes a stable header plus `overall`, `group`, and
  `top-project` rows. Result data goes to stdout; tips/warnings/verbose
  diagnostics go to stderr.
- `SessionSource` is a protocol with `PiSessionSource` as the only adapter;
  moving pi parsing into `sources/pi.py` remains Phase 7 work.

## 9. Phase 4 — Daily refresh workflow (next major phase)

### 4.1 `refresh` command

Target interface:

```bash
openrouter-routing refresh --days 7              # dry run
openrouter-routing refresh --days 7 --apply      # explicit write
openrouter-routing refresh --target ds4-flash --apply
```

Required behavior:

1. Load config and select targets.
2. Measure the configured window, optionally per target/project/model.
3. Resolve aliases and rank endpoints with the configured filters.
4. Print a diff versus the current order and projected `C_eff`.
5. If `--apply` and nothing changed, do nothing.
6. If `--apply` and changed, write through the target backend.
7. Record last run, mix, and applied order under XDG state.
8. Use a lock file to prevent overlapping manual/timer runs.
9. Return non-zero on network/config/write failures.

Acceptance: one command can safely refresh routing daily; repeated runs do not
create version noise; a failed run leaves the current routing untouched.

### 4.2 Target backends

Preset target (existing path):

- [ ] Wrap `preset` functions behind a `Target` backend.
- [ ] Preserve current config fields and no-op behavior.
- [ ] Add tests for apply/diff/no-op and system-prompt warning.

Local pi target:

- [ ] Add `kind = "pi-model"` backend that updates
      `~/.pi/agent/models.json` `compat.openRouterRouting`.
- [ ] Preserve all other provider/model fields and unknown keys.
- [ ] Write atomically with a timestamped backup.
- [ ] Validate JSON before replacing.
- [ ] Verify request-level `provider` precedence over a preset with a controlled
      test request before making it the default target.

Acceptance: the user can choose preset or local pi routing per target, with no
silent cross-target interference.

### 4.3 Multi-target and routing modes

- [ ] Support multiple `[[target]]` blocks with per-target mix filters.
- [ ] Default per-target mix can be selected by model, routed model, and/or
      project tree.
- [ ] Add `routing_mode = "order" | "preferred"`.
  - `order`: current cache-aware pinning.
  - `preferred`: `sort: price` plus hard `ignore`/quantization and soft
    `preferred_*` floors, preserving OpenRouter sticky routing.
- [ ] Emit a clear warning that `order` disables sticky routing.
- [ ] Add a comparison mode that estimates the last N days under both modes.

### 4.4 Safety and observability

- [ ] `doctor` command: config path, pi dir, session count, API key source,
      endpoint reachability, target validity.
- [ ] `--dry-run` is the default for every mutating command.
- [ ] State file records schema version, timestamp, target, mix, old/new order.
- [ ] Optional `--log-file`; systemd uses journald.
- [ ] Tests for atomic writes, backups, locks, and failed network calls.

## 10. Phase 5 — Opt-in automation (systemd user units)

Nothing in this phase is enabled by default. No unit files are installed by
`uv sync`, `config init`, or any ordinary command.

Planned command:

```bash
openrouter-routing install-systemd --mode daily          # print/dry run
openrouter-routing install-systemd --mode daily --enable # explicit opt-in
```

### 5.1 Unit files

Tracked templates:

```text
systemd/openrouter-routing.service
systemd/openrouter-routing.timer
```

Timer semantics:

- `OnStartupSec=2min` so a login triggers a refresh.
- `OnUnitActiveSec=1d` for long-running sessions.
- `Persistent=true` to catch missed runs.
- `RandomizedDelaySec` to avoid a thundering herd.
- `WantedBy=timers.target`.

Service semantics:

- `Type=oneshot`
- `ExecStart` points at the installed `openrouter-routing refresh --apply`
  binary (or the repo `.venv` binary if explicitly requested).
- `WorkingDirectory` set to the repository only if needed.
- `StandardOutput=journal`.

### 5.2 Installer

- [ ] `--print` mode emits units without writing.
- [ ] `--enable` is required to run `systemctl --user daemon-reload` and
      `enable --now`.
- [ ] Refuse to overwrite existing units without `--force`.
- [ ] Document `loginctl enable-linger` for runs while logged out.
- [ ] Explain the interaction between login-triggered and daily runs.
- [ ] Uninstall path: `--disable`/`--remove`.

Acceptance: a fresh clone + normal commands never enables a timer; opting in is
an explicit, documented action with a reversible uninstall.

## 11. Phase 6 — Verification, cost validation, hardening

### 6.1 `verify` command

- [ ] Read recent pi messages with `responseId`.
- [ ] Fetch `GET /api/v1/generation?id=...`.
- [ ] Compare predicted `C_eff` with actual `total_cost`.
- [ ] Report provider, cached tokens, cache discount, and cache writes.
- [ ] Flag likely causes of divergence:
      cache TTL, minimum cacheable prompt, truncation, cache-write pricing.
- [ ] Support `--last N`, `--since`, `--model`, `--project`.
- [ ] Handle missing/expired generation records gracefully.
- [ ] Tests with recorded JSON fixtures; no live network by default.

### 6.2 Cost and routing comparisons

- [ ] `cheapest --compare-current` for a preset: show current order vs proposed
      order with estimated per-1M-token and 7-day costs.
- [ ] `refresh --compare` summary before apply.
- [ ] Optional weekly summary of cache-hit share and effective cost.
- [ ] Validate `order` vs `preferred`/sticky routing assumptions with real
      `generation` data over a week.

### 6.3 Release hardening

- [ ] Add `uv build` to CI.
- [ ] Add coverage reporting (threshold or informational).
- [ ] Add `CHANGELOG.md` before the first tagged release.
- [ ] Add `v0.2.0` tag after Phase 4/5.
- [ ] Consider a PyPI release only if installation via `uv tool install` from
      GitHub is insufficient.

## 12. Phase 7 — Extensibility (deferred)

Only start when there is a real second harness.

- [ ] Define a `SessionSource` protocol:
  - `discover()`
  - `iter_records()`
  - normalized timestamp/model/routed-model/cwd/usage fields
- [ ] Move pi parsing into `sources/pi.py`.
- [ ] Keep pricing/routing harness-agnostic.
- [ ] Add a source selector in config.
- [ ] Document the adapter contract and fixture requirements.

## 13. Testing strategy

- Unit tests for pure logic: pricing, mix, filters, config, routing, preset.
- Fixture sessions under `tests/fixtures/sessions/`.
- Injected HTTP clients; no network in default test runs.
- CLI tests against fixture paths and temp configs.
- Optional network tests marked separately and skipped by default.
- CI matrix: Python 3.11–3.14.
- For every mutating feature: dry-run test, apply test, no-op test, failure test.

Definition of done for a phase:

1. Code and tests are committed.
2. `uv run pytest`, `uv run ruff check .`, and
   `uv run ruff format --check .` pass.
3. CI is green.
4. README/docs, AGENTS, and PLAN are updated if behavior or assumptions
   changed.
5. No new runtime dependency unless explicitly justified.
6. No implicit state mutation or systemd enablement.

## 14. Open questions

1. Should the primary apply target be the remote preset or local pi
   `models.json`? Current default is preset; local pi is technically cleaner for
   a pi-only workflow but needs precedence verification.
2. Default refresh window: 7 days is proposed because it is stable; 3 hours is
   useful for a single dense session but noisy for daily automation.
3. Should `refresh` use `anchor=now` by default while `measure` keeps
   `anchor=latest`? Proposed: yes for refresh, no for ad-hoc.
4. Multi-target config shape: one target per model, or one target per
   project/model pair? Start simple, extend only with real examples.
5. Should `preferred` mode become the default to preserve sticky routing, or is
   explicit `order` worth losing stickiness for cache-price certainty?
6. How much cost history should be stored under XDG state? Proposed: last run
   only plus the last applied order, not a database.

## 15. Quick command reference

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build

uv run openrouter-routing measure --days 7 --by project
uv run openrouter-routing measure --days 30 --project ~/code --json
uv run openrouter-routing cheapest '~deepseek/deepseek-flash-latest' --mix examples/mix.json
uv run openrouter-routing cheapest deepseek/deepseek-v4-flash --mix examples/mix.json --provider-json
uv run openrouter-routing preset show ds4-flash --versions 5
uv run openrouter-routing preset plan ds4-flash --mix examples/mix.json --top 4
uv run openrouter-routing config path
uv run openrouter-routing config init
uv run openrouter-routing config show
```
