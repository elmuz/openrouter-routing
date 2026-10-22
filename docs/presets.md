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

Authentication uses `OPENROUTER_API_KEY`, falling back to the OpenRouter entry
in `~/.pi/agent/auth.json`. `show` and `plan` without `--apply` are read-only.

Endpoint ranking and filtering are documented in [Routing](routing.md).
