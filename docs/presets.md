# Presets

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

`plan --routing-mode preferred` keeps sticky routing: it removes
`provider.order`, sets `provider.sort = {by = "price"}`, and emits soft
`preferred_*` floors instead of hard performance filters. `--dry-run` is an
explicit form of the default read-only behavior.

`show` and `plan` without `--apply` are read-only and need only an inference
credential. Writes need a **read-write management key**: OpenRouter rejects
inference keys on `POST /presets/{slug}/chat/completions` with
`401 Invalid management key`, and a management key cannot run completions.
Configure it as `OPENROUTER_MANAGEMENT_KEY` or as `management_key` under
`[openrouter]` in `~/.config/openrouter-routing/credentials`; see
[Configuration](configuration.md#credentials).

> **Behaviour change (November 2026).** This route accepted inference keys until
> 2026-11-06 and now requires a management key. Keys did not change on the
> client side, so an existing setup starts failing with `401 Invalid management
> key` on the first scheduled run after the switch. `doctor` now reports write
> capability, and `refresh --apply` fails before writing anything with an
explanatory message instead of a bare 401.

For scheduled, multi-target updates use [`refresh`](refresh.md), which wraps this
preset backend and adds mix filters, state, locks, and the local pi target.

Endpoint ranking and filtering are documented in [Routing](routing.md).
