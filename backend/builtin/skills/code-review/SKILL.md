---
name: code-review
description: Review code changes (the working tree, a diff, a branch or files) for bugs, security issues, regressions and maintainability, and report verified findings ranked by severity. Use when the user asks for a review, an audit, a second look, or "what could break".
argument-hint: "[files, branch or focus]"
allowed-tools: [read, grep, glob, repomap, bash]
---

# Code review

Review like a senior engineer who must sign off on production: high signal, verified, no noise.

## Workflow
1. **Scope.** Find what changed: `git status --short`, `git diff --stat`, `git diff` (or the files / branch given in
   `$ARGUMENTS`). Read AGENTS.md / CLAUDE.md / CONTRIBUTING for project rules.
2. **Understand intent.** One sentence: what is this change supposed to do? Read the surrounding code of every hunk
   (callers, types, tests) - never judge a hunk in isolation.
3. **Hunt, by pass** (one pass per lens, each hunk):
   - Correctness: logic errors, off-by-one, null/undefined, wrong conditions, async/await misuse, race conditions,
     error paths that swallow failures, resource leaks.
   - Security: injection (SQL, shell, path traversal, XSS), secrets in code or logs, missing authz checks,
     unsafe deserialization, SSRF, weak crypto.
   - Contracts: changed signatures/schemas without updating callers, migrations, API compatibility.
   - Tests: is the new behaviour tested? Do tests assert the right thing? Run them when cheap.
   - Maintainability: duplication, dead code, misleading names - only when it matters.
4. **Verify every finding.** Trace a concrete input or state to the failure. If you cannot, drop it or mark it
   "needs confirmation". Run a quick command (test, script, grep for callers) when it settles the question.
5. **Report**, most severe first:
   ```
   ### [CRITICAL|HIGH|MEDIUM|LOW] path:line - one-line summary
   Failure scenario: concrete input/state -> wrong result
   Fix: the minimal change
   ```
   End with a short verdict: ship / ship after fixes / needs rework, and what you did NOT review.

## Pitfalls
- Style nits drown real bugs: skip them unless the project's rules require them.
- "Might be a problem" without a scenario is not a finding.
- Do not modify files during a review unless the user asked for fixes.
