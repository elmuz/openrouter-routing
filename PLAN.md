# Development plan and roadmap

This document records the plan followed so far and the detailed work remaining.
It is intentionally implementation-oriented: every phase lists deliverables,
acceptance criteria, and the decisions that should not be silently changed.

Repository: <https://github.com/elmuz/openrouter-routing>
Last updated: 2026-10-23.

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
| 4 | `refresh` workflow and local pi target | Done |
| 5 | Opt-in systemd automation | Done |
| 6 | Cost verification and hardening | Done |
| 7 | Other harness adapters | Deferred / low priority |

Phase 3 work is on top of `1338379 Initial Python package with XDG config and tests`.
Phase 4 adds the refresh workflow, target backends, state/locks, and `doctor`.
Phase 5 adds the opt-in systemd user timer installer and tracked unit templates.
Phase 6 adds generation-record verification, current-order comparisons, and release hardening.

Validation at the time of writing:

```text
uv run pytest                    127 passed
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
uv run openrouter-routing verify --last 3
```

Phase 4 tests inject HTTP clients, temporary session files, and temporary
`models.json` files, so the whole refresh/target/state/doctor path is covered
offline. No OpenRouter preset was modified during this work; live `refresh
--apply` remains a manual action.

Phase 5 tests inject a fake `run_command`, so the installer never runs
`systemctl` in the default suite. The dry-run, enable, overwrite, failure,
disable, and remove paths are covered.

Phase 6 tests use recorded generation fixtures and a fake OpenRouter client.
No generation API call is made by the default test suite. Coverage reporting is
informational in CI; the local run is 85% with `coverage run -m pytest`.

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
├── automation.py   opt-in systemd user unit installer
├── cli.py          argparse CLI: measure / cheapest / preset / refresh / verify / config
├── config.py       TOML config and dataclasses
├── errors.py       expected user-facing errors
├── mix.py          filtering, time windows, aggregation, grouping
├── paths.py        XDG and pi-agent paths
├── preset.py       preset config planning and field preservation
├── pricing.py      endpoint scoring and percentile helpers
├── routing.py      filtering, ranking, provider blocks, mode comparisons
├── sessions.py     pi session JSONL parsing
└── verify.py       generation-record cost verification
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
| Systemd | preview by default; opt-in `--enable` only | User explicitly requested opt-in automation |
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
- Targets are parsed and exposed through `config show`; Phase 4 later consumes
  them in the refresh workflow.

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

## 9. Phase 4 — Daily refresh workflow (done)

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

- [x] Wrap `preset` functions behind a `Target` backend.
- [x] Preserve current config fields and no-op behavior.
- [x] Add tests for apply/diff/no-op and system-prompt warning.

Local pi target:

- [x] Add `kind = "pi-model"` backend that updates
      `~/.pi/agent/models.json` `compat.openRouterRouting`.
- [x] Preserve all other provider/model fields and unknown keys.
- [x] Write atomically with a timestamped backup.
- [x] Validate JSON before replacing.
- [x] Verify request-level `provider` precedence over a preset with a controlled
      test request before making it the default target.

Implementation note: the preset target remains the default/recommended target.
Request-level precedence is documented but has not been verified with a live
controlled request, so `pi-model` is available but not the default.

Acceptance: the user can choose preset or local pi routing per target, with no
silent cross-target interference.

### 4.3 Multi-target and routing modes

- [x] Support multiple `[[target]]` blocks with per-target mix filters.
- [x] Default per-target mix can be selected by model, routed model, and/or
      project tree.
- [x] Add `routing_mode = "order" | "preferred"`.
  - `order`: current cache-aware pinning.
  - `preferred`: `sort: price` plus hard `ignore`/quantization and soft
    `preferred_*` floors, preserving OpenRouter sticky routing.
- [x] Emit a clear warning that `order` disables sticky routing.
- [x] Add a comparison mode that estimates the last N days under both modes.

### 4.4 Safety and observability

- [x] `doctor` command: config path, pi dir, session count, API key source,
      endpoint reachability, target validity.
- [x] `--dry-run` is the default for every mutating command.
- [x] State file records schema version, timestamp, target, mix, old/new order.
- [x] Optional `--log-file`; systemd uses journald.
- [x] Tests for atomic writes, backups, locks, and failed network calls.

### 4.5 Implementation notes

- `refresh` plans every selected target before writing any of them, so a network,
  config, or ranking failure leaves all targets untouched.
- The refresh window defaults to `[refresh] days = 7`, `anchor = "now"`; ad-hoc
  `measure` keeps `anchor = "latest"`.
- Per-target filters are the only mix selectors for refresh (no implicit
  inheritance from `[measure]`), which keeps each target explicit.
- `--apply` with no routing change skips the target write. State is still
  updated for a successful apply run so the last run is observable.
- State lives in `${XDG_STATE_HOME:-~/.local/state}/openrouter-routing/state.json`
  with schema `openrouter-routing/state/v1`. The lock is in the same directory.
- The comparison's preferred estimate approximates `sort: price` by minimizing
  uncached input plus output price; OpenRouter's exact tie-breaking is not public.
- The local `pi-model` backend patches only
  `providers.<provider>.models[<id>].compat.openRouterRouting`, validates the
  serialized JSON, copies a timestamped backup, and then atomically replaces
  `models.json`.

## 10. Phase 5 — Opt-in automation (systemd user units) (done)

Nothing in this phase is enabled by default. No unit files are installed by
`uv sync`, `config init`, or any ordinary command.

Commands:

```bash
openrouter-routing install-systemd --mode daily          # print/dry run
openrouter-routing install-systemd --mode daily --print  # explicit preview
openrouter-routing install-systemd --mode daily --enable # explicit opt-in
openrouter-routing install-systemd --disable             # keep files, stop timer
openrouter-routing install-systemd --remove              # disable + delete
```

### 5.1 Unit files

Tracked templates:

```text
systemd/openrouter-routing.service
systemd/openrouter-routing.timer
```

The wheel includes the templates as `openrouter_routing/systemd/` via a Hatch
force-include, so an installed copy does not depend on the repository layout.

Timer semantics:

- `OnStartupSec=2min` so a login triggers a refresh.
- `OnUnitActiveSec=1d` for long-running sessions.
- `Persistent=true` to catch missed runs.
- `RandomizedDelaySec=15min` to avoid a thundering herd.
- `AccuracySec=1min`.
- `WantedBy=timers.target`.

Service semantics:

- `Type=oneshot`
- `ExecStart` points at the installed `openrouter-routing refresh --apply`
  binary, with the absolute `--config` path captured at install time. The
  default falls back to the repo `.venv` binary or `python -m openrouter_routing`;
  `--binary` overrides it.
- No `WorkingDirectory` is needed; the config path is explicit.
- `StandardOutput=journal` and `StandardError=journal`.

### 5.2 Installer

- [x] `--print` mode emits units without writing.
- [x] Plain `install-systemd` is also a preview/dry run.
- [x] `--enable` is required to write units, run
      `systemctl --user daemon-reload`, and `enable --now` the timer.
- [x] Refuse to overwrite existing units without `--force`.
- [x] Document `loginctl enable-linger` for runs while logged out.
- [x] Explain the interaction between login-triggered and daily runs.
- [x] Uninstall path: `--disable`/`--remove`; `--remove` implies `--disable`.
- [x] `--unit-dir` and `--binary` overrides for non-standard installations.

Implementation notes:

- Units live in `${XDG_CONFIG_HOME:-~/.config}/systemd/user/`.
- `automation.py` renders the templates and takes an injected command runner,
  so tests never touch the real user manager.
- The installer captures the config path because a systemd user service does not
  inherit the interactive shell's `XDG_CONFIG_HOME` or
  `OPENROUTER_ROUTING_CONFIG`.
- `OPENROUTER_API_KEY` is not inherited either; `docs/automation.md` documents
  using `~/.pi/agent/auth.json` or an explicit user-manager environment.
- `--remove` disables the timer, deletes both unit files, then runs
  `daemon-reload`; a subsequent install is again an explicit opt-in.

Acceptance: a fresh clone + normal commands never enables a timer; opting in is
an explicit, documented action with a reversible uninstall.

## 11. Phase 6 — Verification, cost validation, hardening (done)

### 6.1 `verify` command

- [x] Read recent pi messages with `responseId` (also accepts `response_id`,
      `generationId`, and `generation_id`).
- [x] Fetch `GET /api/v1/generation?id=...` through `OpenRouterClient`.
- [x] Compare predicted `C_eff` with actual `total_cost`, using pi's token
      counters and the generation provider's endpoint prices.
- [x] Report provider, cached tokens, cache discount, and cache writes.
- [x] Flag likely causes of divergence: cache TTL/minimum prompt, missing
      cache pricing, cache-write pricing, truncation/tokenizer differences,
      BYOK/upstream cost, and missing cache discounts.
- [x] Support `--last N`, `--since`, `--model`, `--project`, plus
      `--routed-model`, `--provider`, exclusions, and `--tolerance`.
- [x] Handle missing/expired generation records gracefully: 404 is `missing`,
      not a fatal error; the command exits non-zero only when nothing can be
      verified or a non-404 lookup fails.
- [x] Tests with recorded JSON fixtures; no live network by default.

### 6.2 Cost and routing comparisons

- [x] `cheapest --compare-current --preset SLUG`: show the first still-available
      endpoint in the current order versus the proposed endpoint, with
      per-1M-token `C_eff` and measured-mix costs.
- [x] `refresh --compare` summary before apply (implemented in Phase 4).
- [x] Weekly summary of cache-hit share and effective cost through the `verify`
      summary (`f`, `R`, `fw`, actual `C_eff`, cache discount, provider mix)
      over a `--since` window.
- [x] Validate `order` versus `preferred`/sticky routing with real generation
      data: `verify` reports provider distribution and actual `C_eff`; the
      documented weekly workflow compares it with the `refresh --compare`
      estimate.

### 6.3 Release hardening

- [x] Add `uv build` to CI.
- [x] Add informational coverage reporting with `coverage run -m pytest` and
      `coverage report` (85% locally at the time of writing).
- [x] Add `CHANGELOG.md` before the first tagged release.
- [x] Bump the package to `0.2.0` after Phase 4/5/6.
- [ ] Create the `v0.2.0` git tag after the changes are committed and CI is
      green (manual release step; this working tree is not committed here).
- [x] No PyPI release: installation from GitHub with `uv tool install` is
      sufficient for now.

Implementation notes:

- `OpenRouterNotFound` distinguishes an expired generation record from other
  HTTP failures.
- Endpoint matching uses `provider_name`, then the tag prefix, and prefers an
  endpoint with cache-read pricing when the session has cache reads. Multiple
  quantizations of one provider are resolved deterministically by tag; the
  generation record does not expose the chosen quantization.
- `verify` defaults to the last 20 generations when no time window is given, so
  an ad-hoc run does not issue thousands of API calls.
- The comparison mix for `cheapest --compare-current` uses the saved token
  counters when present; otherwise it synthesizes one million input tokens from
  the selected `f`/`R`/`fw`, so `C_eff` and the mix cost agree.
- CI now runs lint, format check, coverage, and `uv build` on Python 3.11–3.14.

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

Resolved in Phase 4:

1. Primary apply target: the preset remains the default/recommended target;
   `pi-model` is available per target but its request-level precedence over a
   preset still needs a controlled live check before it becomes the default.
2. Refresh window: `days = 7` from `[refresh]`.
3. Refresh anchor: `anchor = "now"` for refresh; `measure` keeps `"latest"`.
4. Multi-target shape: one `[[target]]` per destination, with optional per-target
   model/routed-model/project filters. No nested project/model matrix yet.
5. Routing mode: `order` stays the default with an explicit sticky-routing
   warning; `preferred` is opt-in and comparable with `--compare`.
6. State history: last run only (window, target, mix, old/new orders), not a
   database.

Still open:

1. When should `pi-model` become the recommended default target? Verify a
   controlled request where both a preset and `compat.openRouterRouting` set
   routing, then document/apply the result.
2. Should a future `refresh` keep an opt-in tiny history (e.g. last N runs) for
   trend checks, or is last-run state enough?
3. Should `preferred` expose a per-target `max_price` guardrail, given that
   `max_price` has no cache-read field?

## 15. Quick command reference

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run coverage run -m pytest && uv run coverage report
uv build

uv run openrouter-routing measure --days 7 --by project
uv run openrouter-routing measure --days 30 --project ~/code --json
uv run openrouter-routing cheapest '~deepseek/deepseek-flash-latest' --mix examples/mix.json
uv run openrouter-routing cheapest deepseek/deepseek-v4-flash --mix examples/mix.json --provider-json
uv run openrouter-routing cheapest --preset ds4-flash --mix examples/mix.json --compare-current
uv run openrouter-routing preset show ds4-flash --versions 5
uv run openrouter-routing preset plan ds4-flash --mix examples/mix.json --top 4
uv run openrouter-routing refresh --target ds4-flash
uv run openrouter-routing refresh --target ds4-flash --apply
uv run openrouter-routing refresh --compare --apply
uv run openrouter-routing verify --last 20
uv run openrouter-routing verify --since 2026-10-01T00:00:00Z --json
uv run openrouter-routing doctor
uv run openrouter-routing install-systemd --mode daily
uv run openrouter-routing install-systemd --mode daily --enable
uv run openrouter-routing install-systemd --disable
uv run openrouter-routing install-systemd --remove
uv run openrouter-routing config path
uv run openrouter-routing config init
uv run openrouter-routing config show
```
