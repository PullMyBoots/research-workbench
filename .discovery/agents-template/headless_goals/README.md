# Headless Dashboard Goals

These goals are used by the Discovery dashboard's headless `codex exec` runner.
Each file is a thin wrapper around the corresponding authoritative TUI goal in
`goals/`. Research semantics, evidence bars, completion conditions, and role
boundaries live only in the regular goal. Headless wrappers add the continuation
mechanics required for unattended execution.

Do not paste these files into a manual `/goal` TUI session unless you are
deliberately testing the headless behavior. Manual TUI sessions should use:

- `goals/route_builder.md`
- `goals/route_auditor.md`
- `goals/route_debug_eval.md`

Dashboard/headless Builder task:

```text
codex exec Follow the instructions in ./headless_goals/route_builder.md
```

Dashboard/headless Auditor task:

```text
codex exec Follow the instructions in ./headless_goals/route_auditor.md
```

Dashboard/headless debug failed formal eval task:

```text
codex exec Follow the instructions in ./headless_goals/route_debug_eval.md
```

The dashboard chooses the correct headless goal from `.discovery/loop_state.json`.
