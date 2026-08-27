# Manual Goal Startup

This workspace does not use Codex lifecycle hooks for loop control.

Each manual role goal follows the same three-part contract:

1. `背景与任务描述`: role, evidence inputs, required workflow, and research
   methodology;
2. `完成定义与验收`: the only evidence that proves the role run is complete;
3. `工作范围与约束注意事项`: public/private boundaries, role separation,
   evaluation integrity, resource ownership, and continuation boundaries.

Start Codex, select the role required by loop state, then paste one goal
manually.

Builder task:

```text
/goal Follow the instructions in ./goals/route_builder.md
```

Auditor task:

```text
/goal Follow the instructions in ./goals/route_auditor.md
```

Debug failed Candidate Check task:

```text
/goal Follow the instructions in ./goals/route_debug_eval.md
```

Use the Builder task when `.discovery/loop_state.json` says `phase: work_loop`
and no eval is queued/running. Use the debug task when `phase: work_loop` and
`eval_status: check_failed`. Formal-evaluator failures require Human/Main
review. Use the Auditor task after successful formal eval moves the state to
`reflection_loop`.
