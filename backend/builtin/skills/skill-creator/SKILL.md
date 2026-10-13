---
name: skill-creator
description: Create or improve an Agent Skill (a SKILL.md folder) for DOINg.AI SuperCode. Use when the user asks to write, package, refine or review a skill, a playbook or reusable instructions for the agent.
argument-hint: "<what the skill should teach>"
---

# Skill creator

A skill is a folder with a `SKILL.md` and optional reference files or scripts. The agent sees only each
skill's **name + description** until a task matches; then it loads the body (progressive disclosure).
So the description decides whether the skill is ever used, and the body decides whether it helps.

## Where skills live
- Project (shared with the repo): `<project>/.supercode/skills/<name>/SKILL.md` (also read: `.claude/skills`, `.agents/skills`).
- User (all projects): `data/skills/<name>/SKILL.md` - managed in **Settings > Skills**.

## Workflow
1. **Pin the use cases.** Write 2-3 concrete requests the skill must handle and one it must NOT trigger on.
2. **Name**: lowercase-hyphenated, verb or domain first (`api-endpoint`, `release-notes`), max 64 chars.
3. **Description** (max 1024 chars, third person): WHAT it does + WHEN to use it, with the trigger words users
   actually type ("Use when the user asks to ..."). Vague descriptions never trigger.
4. **Body**: imperative, expert, concise.
   - `# Title`, then "When to use" (one paragraph) and a numbered **workflow**.
   - Exact commands, file conventions, templates, checklists, a **definition of done**.
   - **Pitfalls** section: the mistakes a capable but uninformed agent would make.
   - Keep the body under ~400 lines. Move long material (API tables, large examples) to
     `references/<topic>.md` and say exactly when to read each file.
   - Scripts go in `scripts/`; tell the agent how to run them and what they output.
5. **Frontmatter options** (all optional):
   - `argument-hint: "<file>"` - shown in the `/skill-name` menu; the user's text replaces `$ARGUMENTS` in the body.
   - `allowed-tools: [read, grep, bash]` - the tools the workflow relies on.
   - `disable-model-invocation: true` - only the user can run it (`/name`), the agent never auto-loads it.
   - `user-invocable: false` - hidden from the `/` menu; only the agent loads it.
6. **Test it**: run `/name <a realistic request>` in a session, watch where the agent hesitates or deviates,
   tighten those steps. Re-test on the "must not trigger" request with a plain prompt.

## Template
```markdown
---
name: my-skill
description: Does X for Y. Use when the user asks to ..., mentions ..., or when ... .
argument-hint: "<target>"
---

# My skill

When to use: ...

## Workflow
1. ...
2. ...

## Definition of done
- ...

## Pitfalls
- ...
```

## Quality checklist
- [ ] Description names concrete triggers and the output.
- [ ] Every step is actionable (a command, a file, a check), no generic advice ("write good code").
- [ ] Project-specific facts (paths, commands) are correct for this repository.
- [ ] Long content lives in references/ files, each referenced with "read when ...".
- [ ] No secrets, tokens or personal data in the skill.
