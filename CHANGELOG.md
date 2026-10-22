# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning once releases are tagged.

## [0.2.0] - Unreleased

### Added

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
