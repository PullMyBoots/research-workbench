---
name: chatgpt-handoff
description: Prepare and publish self-contained GitHub handoffs for manually run ChatGPT web Pro or Deep Research tasks. Use when the user explicitly requests a handoff; suggest it when a task needs broad cited web research, an independent high-budget review, or help breaking a critical deadlock, but never publish until the user confirms.
---

# ChatGPT Handoff

## Choose the recipient

- Treat **Deep Research** as an external research team. Use it to search broadly across the web, synthesize papers, datasets, projects, and other sources, and produce a cited research report.
- Treat **Pro** as an external senior reviewer. Use it to independently audit consequential conclusions, challenge a mature approach, compare high-stakes decisions, or help break a persistent deadlock when the relevant evidence is already assembled.
- Keep implementation, experiments, tests, and directly verifiable questions in Codex. Do not use Pro as a substitute for local verification or Deep Research for routine questions that do not depend on external sources.

## Workflow

1. Select Pro or Deep Research from the user's request. When suggesting a handoff, name the recommended recipient, explain why, and wait for confirmation.
2. Assign one stable Item id and create the consultation from the start under
   `.DiscoveryProgram/knowledge/items/<item-id>/`. Write
   `.handoff-pending.json` there so the unregistered directory is visibly
   incomplete. Do not add it to `items.json` yet.
3. Store the original question and consultation provenance in the Item
   directory. Build its publishable package under `handoff/` with exactly
   `README.md` and `workspace/`. Do not alter the source project's `.git`,
   branches, or remotes.
4. Include the problem, context, relevant project files, current progress,
   existing evidence, constraints, and expected output required to complete
   the task.
5. Make `handoff/` understandable without access to the current conversation
   or local environment. Run the publisher with `--dry-run` before publishing.
6. After publishing, record the relay repository URL and snapshot or commit
   identity in the same Item directory. Return the URL, recommended recipient,
   and a ready-to-paste launch prompt. Stop there; never operate the ChatGPT
   website automatically.
7. Wait for Human to save the complete Web result into this same Item directory
   and provide its path. A question or published handoff without the returned
   result is not a complete Item and must remain absent from `items.json`.
8. Confirm that the Item directory now contains the original question,
   published `README.md` and `workspace/`, repository snapshot or commit
   identity, complete returned result, citations, recipient, and dates.
9. Use `$maintain-discovery`: remove `.handoff-pending.json`, write the Item
   title and about-200-word summary with applicability and evidence limits,
   register the existing directory as a Topic Item, and update affected Topic
   syntheses.
10. Read the returned result, verify it against local code, tests, and Practice,
    then continue the task. Cite the external package as `@item:<id>` and,
    when local verification makes material progress, add a `@memory:<id>` log
    and update Main Memory as appropriate; formal Route evidence stays in
    `@version:<id>`.

For another consultation, rebuild the package from scratch and replace the relay repository. Never carry content from a previous task forward.

## Repository protocol

Publish one isolated, self-contained task snapshot at a time from the pending
Topic Item's `handoff/` directory:

```text
README.md
workspace/
```

- Use `README.md` as the only entry point. State the recipient, objective, context, key questions, constraints, expected output, and material map.
- Put all required code, documents, data, logs, and other materials in `workspace/`, preserving useful source-project structure where practical.
- Include enough material to understand and complete the task, but exclude irrelevant content.
- Exclude the pending marker, returned-result files, prior-task content, local
  absolute paths, `.git`, dependency caches, build artifacts, access tokens,
  secrets, and credentials.
- Ensure the recipient can understand and execute the task using only the published repository.

Use this `README.md` template, written in the user's active language:

```markdown
# Task Handoff

Recipient: Pro / Deep Research

## Objective

State what must be completed and what constitutes success.

## Context

Describe the background, current progress, existing conclusions, attempted approaches, and encountered problems.

## Key questions

List the questions the external recipient must answer explicitly.

## Constraints

State the scope, technical constraints, and approaches to avoid.

## Expected output

Specify the output format, level of detail, and required content.

## Material map

List the important paths under `workspace/`, their purpose, and the recommended reading order.
```

For Deep Research, require traceable citations, source links, conflicting evidence, and identified information gaps. For Pro, require an independent judgment, counterexamples or risks, alternatives, prioritized recommendations, and unresolved uncertainties.

## Configure and publish

Resolve `scripts/publish_handoff.py` relative to this `SKILL.md` and use that absolute path as `PUBLISHER`.

If the publisher reports missing configuration on first use, ask the user for a dedicated relay repository URL. Do not guess the repository or reuse the source project's remote. Then run:

```bash
python3 "$PUBLISHER" configure --remote-url git@github.com:OWNER/REPOSITORY.git
```

The publisher stores configuration outside the skill and never stores authentication credentials. Validate locally without contacting the remote:

```bash
python3 "$PUBLISHER" publish --dry-run /path/to/task-package
```

Publish only after validation succeeds:

```bash
python3 "$PUBLISHER" publish /path/to/task-package
```

The publisher force-updates the configured branch with a fresh root commit. This replaces the branch's visible content and history, but does not guarantee permanent deletion from the hosting provider's backend.
