# Headless Route Auditor

Follow every research, evidence, completion, and boundary instruction in
`goals/route_auditor.md`. That file is authoritative; this wrapper adds only the
rules needed for unattended execution.

- Run without interactive nudges and treat `./explore context` plus the Runtime-
  injected job facts as authoritative current state.
- For a detached handoff or wait timeout, write the required Continuation
  Checkpoint in the current `reflection.md` draft and end this Turn. Runtime
  starts a fresh Auditor Turn only after the associated Job is terminal.
- Never resubmit an existing Job, mix roles, or poll while Runtime owns waiting.
- End after `./explore reflect` succeeds, a legitimate continuation handoff is
  recorded, or the regular goal requires a stop. Do not invent another
  completion condition.
