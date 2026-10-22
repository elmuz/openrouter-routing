# Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The runtime has no third-party dependencies. Tests use fixture sessions and
injected HTTP clients, so the default test run never touches the network.

## Repository layout

```text
src/openrouter_routing/            importable package (CLI, pricing, sessions, API)
  refresh.py targets.py state.py   refresh workflow, target backends, XDG state
  verify.py                        generation-cost verification
  automation.py                    opt-in systemd user unit installer
  fsutil.py doctor.py              atomic writes/backups, diagnostics
measure.py cheapest.py preset.py   legacy 1:1 wrappers around the CLI
tests/                             pytest suite (offline)
docs/                              user documentation (this folder)
examples/                          sample mix JSON, not a runtime input
systemd/                           canonical systemd user unit templates
```

## Contributing

`AGENTS.md` documents the repository invariants (read-only by default, preserve
unknown preset fields, skip no-op writes, no network in tests, no secrets).
`PLAN.md` has the phase plan and roadmap. Phase 4 (`refresh`, target backends,
`doctor`, state/locks) is documented in [Refresh](refresh.md), the Phase 5
opt-in systemd timer in [Automation](automation.md), and Phase 6 generation
verification in [Verify](verify.md).

If you change the cost formula or billing assumptions, update
[`how-it-works.md`](how-it-works.md), the agent skill reference under
`.agents/skills/openrouter-cost-routing/`, and the tests in the same change.
