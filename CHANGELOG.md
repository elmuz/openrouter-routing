# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning once releases are tagged.

## [0.2.0] - Unreleased

### Added

- Separate write credential: preset writes use `OPENROUTER_MANAGEMENT_KEY` or
  `[openrouter] management_key` in `~/.config/openrouter-routing/credentials`,
  and never fall back to the inference key. OpenRouter started rejecting
  inference keys on `POST /presets/{slug}/chat/completions` with
  `401 Invalid management key` in November 2026, which made every scheduled
  `refresh --apply` fail silently in the journal while the plan kept printing.
  A missing write key now fails before the network call with instructions.
- `doctor` reports write capability separately (`auth write`) by asking
  `GET /api/v1/key` for `is_management_key`; inference keys look identical to
  management keys by prefix, so the flag is the only reliable check.
- `compare MODEL_A MODEL_B --mix mix.json`: rank the cheapest endpoint of two
  models with the same measured token mix and report which is cheaper and by
  how much. The mix is a workload fingerprint, so a model with no history can
  be priced against your real usage. The output always carries a rough-estimate
  caveat (tokenizer, caching, output-length, quality, and context differences).
- `verify`: compare the cost predicted from pi session tokens and OpenRouter
  endpoint prices with the actual `total_cost` from
  `GET /api/v1/generation?id=...`. Reports provider, cached tokens, cache
  discount, cache writes, predicted/actual `C_eff`, and likely divergence
  causes (cache TTL/minimum prompt, missing cache pricing, cache-write pricing,
  truncation, BYOK/upstream cost).
- `cheapest --compare-current --preset SLUG`: compare a preset's current
  `provider.order` with the proposed best endpoint, with per-1M-token and
  measured-mix costs.
- `install-systemd`: opt-in systemd user timer with preview, enable, disable,
  and remove paths. Nothing is installed or enabled by default.
- `refresh`: preset and local pi `models.json` targets, `order`/`preferred`
  routing modes, XDG state, locking, `--log-file`, and `doctor`.

### Changed

- Credentials resolve as: reads `OPENROUTER_API_KEY` → `credentials`
  `inference_key` → `~/.pi/agent/auth.json`; writes `OPENROUTER_MANAGEMENT_KEY`
  → `credentials` `management_key`. Project keys stay out of pi's `auth.json`,
  so rotating one no longer affects the other.
- The systemd service template loads
  `EnvironmentFile=-~/.config/openrouter-routing/env` (`-` tolerates a missing
  file) so a timer run can see a management key without inheriting a shell.
- `SessionRecord` now carries `responseId` for generation verification.
- OpenRouter HTTP 404 responses raise `OpenRouterNotFound`, so expired
  generation records are reported instead of aborting a verification run.
- Package version is now `0.2.0`.

### Development

- CI runs lint, format check, tests, coverage reporting, and `uv build`.
- Added `coverage` as a development-only dependency.
- Offline tests cover generation lookup, verification, endpoint matching, and
  current-vs-proposed routing comparisons.

### Release notes

- The `v0.2.0` git tag is created manually after the Phase 4/5/6 changes are
  committed and CI is green.
- A PyPI release is not required while `uv tool install` from GitHub is
  sufficient.
