# Discovery State

State is namespaced by Topic and Problem.

```text
.DiscoveryProgram/
  problem_registry.json       Topic's canonical Problem index and default
  knowledge/                  Unified Topic knowledge
    items/<item-id>/           complete external source bundle
    items.json                 Item ids, names, paths, and summaries
    topics.json                Main-owned external syntheses
  memory/
    main.md                    required Main Agent cross-session memory
    logs/<id>.json             immutable Topic `@memory` entity
  integration/                cross-Problem dependencies and acceptance
  private/                    Topic-private control material
  log/                        cross-Problem administrative logs

subprojects-team/<problem-id>/
  problem.json                Problem identity, readiness, dependencies, contract
  .DiscoveryConsole/
    resources.json            Problem free-run, queue, Eval, scheduler policy
    pub/                      Route-readable Problem state
    private/                  evaluator/Main-Agent-only Problem state
  agent*/                     Route workspaces
```

The Topic registry stores stable `id`, relative `path`, title, lifecycle status,
and approval summary. The Problem's own `problem.json` is authoritative for its
contract and readiness. Problem ids are included in jobs, leases, and newly
recorded Versions. Metrics, rankings, baselines, jobs, and practice are never
combined across Problems.

Problem-shared state uses direct files under its `.DiscoveryConsole/pub/`.

```text
pub/knowledge/
  items/<item-id>/             complete external source bundle
  items.json                   Item ids, names, paths, and summaries
  topics.json                  Main-owned syntheses
  versions/<version-id>.json   one formal Evaluation practice entity

pub/notices.jsonl

pub/log/
  resource.lock               this Problem's scheduler lock
  resource_state.json         this Problem's live leases
  jobs.jsonl                  this Problem's queued and completed jobs
```

Agent loop state lives inside each `agent*/.discovery/loop_state.json`.

```json
{
  "phase": "work_loop",
  "last_version": null,
  "last_reflected_version": null,
  "eval_status": null,
  "active_eval": null,
  "last_error": null
}
```

`phase` is normally `work_loop` or `reflection_loop`. Legacy state may contain
`done`, but current public Route transitions do not expose a command that sets
it.

The CLI and Worker update this state. Codex lifecycle hooks are not used for
loop control. The Dashboard chooses Builder, Auditor, or Debug Eval from the
state and launches the matching `headless_goals/*.md` contract under the Route
permission profile. The Route-local `./explore` client does not provide a
direct-launch fallback.

State transitions:

- `./explore eval` check success -> `work_loop` with `eval_status: queued`;
- worker starts queued formal eval -> `work_loop` with `eval_status: running`;
- worker formal eval success -> `reflection_loop` with `eval_status: succeeded`;
- check failure -> `work_loop` with `eval_status: check_failed` and `last_error`;
- formal eval failure or invalid report -> `work_loop` with `eval_status: failed`,
  `active_eval`, and `last_error`;
- reflection completed -> `work_loop`, previous `notebook.md` archived under
  `notebooks/`, and fresh `notebook.md` written from the next target brief.

Routes inspect the state through `./explore context` and must not edit it.
`./explore eval`, the Worker, and `./explore reflect` are the transition
mechanisms.
