---
name: create-single-agent-project
description: Create one named, blank, long-running single-Agent research project under subprojects-single and initialize only its Human-provided goal. Use when Human/Main choose one independent Agent with a project-local practice, memory, metacognition, and external-knowledge loop, without Exploration Team machinery.
---

# Create Single-Agent Project

Create no project until Human provides both a project id and its initial goal. Never invent the goal.

## Workflow

1. Locate the Topic root containing `.DiscoveryProgram/` and `subprojects-single/`.
2. Normalize the requested project name to a lowercase hyphenated id. Confirm the mapping when normalization could change its meaning.
3. Resolve `scripts/create_single_agent_project.py` relative to this `SKILL.md` and run:

   ```bash
   python3 "$CREATOR" --topic-root <topic-root> --id <project-id> --goal <goal>
   ```

4. Verify that `subprojects-single/<project-id>/` is an independent Git root whose working tree contains only `AGENTS.md` and `.ResearchProject/`, aside from version-control placeholders.
5. Read `.ResearchProject/memory/main.md`. Confirm that only `目标与背景` was initialized, both knowledge indexes remain `{}`, and no Log exists.
6. Return the project path and initialized goal to Human. Start the Project Agent from that directory so the local Git root and `AGENTS.md` define its context; Main does not operate it as a direct-work folder or Team Route.

## Boundaries

- Refuse to overwrite or merge into an existing path.
- Do not create source, experiment, data, result, plan, or environment directories.
- Do not add a Log, external Item, Knowledge Topic, or metacognitive judgment during initialization.
- Do not place the project in `subprojects-main/` or `subprojects-team/`.
- Do not register it in `.DiscoveryProgram/problem_registry.json`; that registry is only for Exploration Team Problems.
- Do not update Topic Main Memory unless Human separately approves that maintenance.
