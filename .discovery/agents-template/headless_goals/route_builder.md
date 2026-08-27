# Headless Route Builder

Follow every research, evidence, completion, and boundary instruction in
`goals/route_builder.md`. That file is authoritative; this wrapper adds only the
rules needed for unattended execution.

- Run without interactive nudges and treat `./explore context` plus the Runtime-
  injected job facts as authoritative current state.
- For a detached handoff or wait timeout, write the required Notebook
  Continuation Checkpoint and end this Turn. Runtime starts a fresh Builder Turn
  only after the associated Job is terminal.
- Never resubmit an existing Job, mix roles, or poll while Runtime owns waiting.
- An evidence-bounded no-Candidate handoff is allowed exactly as defined in the
  regular Builder goal. Leave the loop state unchanged, do not manufacture a
  Candidate, and make the Main decision needed to reopen search explicit;
  Runtime records the clean no-change exit as a blocked Human/Main handoff.
- End after a formal Evaluation is queued, a legitimate continuation handoff is
  recorded, a no-Candidate handoff is recorded, or the regular goal requires a
  stop. Do not invent another completion condition.
