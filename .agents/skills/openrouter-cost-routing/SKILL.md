---
name: openrouter-cost-routing
description: Use when editing OpenRouter pricing, provider routing, prompt-cache accounting, or pi session cost analysis. Verifies assumptions against the official OpenRouter billing/routing docs before changing this repository.
---

# OpenRouter cost routing

Read `README.md` and `AGENTS.md` first. This skill exists to keep pricing and
routing assumptions anchored to current OpenRouter behavior instead of memory.

## Before changing pricing or routing

1. Read `references/openrouter-billing.md` for the fields and endpoints this
   project relies on.
2. Re-check the official docs when the change depends on billing semantics:
   - https://openrouter.ai/docs/features/prompt-caching
   - https://openrouter.ai/docs/use-cases/usage-accounting
   - https://openrouter.ai/docs/features/provider-routing
   - https://openrouter.ai/docs/features/presets
   - https://openrouter.ai/openapi.json
3. Inspect a live response when possible:
   - `GET /api/v1/models/<author>/<slug>/endpoints`
   - `GET /api/v1/generation?id=<responseId>`
   - `GET /api/v1/presets/<slug>` and `/versions` (authenticated)

Do not invent provider fields, pricing fields, or preset write methods. If the
docs and the live OpenAPI disagree, prefer the live schema and note the
difference in `references/openrouter-billing.md`.

## Project invariants

- Rank on `C_eff = (1-f)*p_in + f*p_cr + R*p_out + fw*p_cw`.
- Never score a no-cache endpoint at the measured `f`; collapse `f` and `fw` to
  zero for it.
- `provider.order` disables OpenRouter's automatic sticky routing. Document the
  tradeoff when changing routing modes.
- Preset writes are full-config POSTs and create versions. Preserve unknown
  fields and skip no-op writes unless forced.
- Tests must not touch the network.
- Read-only commands must never mutate a preset or `models.json`.

## Verification workflow

```bash
uv run pytest
uv run ruff check .
uv run openrouter-routing measure --days 7 --by project
uv run openrouter-routing cheapest <model> --mix examples/mix.json
uv run openrouter-routing preset plan <slug> --mix examples/mix.json
```

For an applied plan, compare recent `responseId`s with
`GET /api/v1/generation?id=...` and check `total_cost`,
`native_tokens_cached`, `cache_discount`, and `provider_name`. A mismatch usually
means cache TTL, minimum cacheable prompt size, truncation, or cache-write
pricing differs from the assumed mix.
