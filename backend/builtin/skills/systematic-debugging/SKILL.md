---
name: systematic-debugging
description: Find and fix the root cause of a bug, test failure, crash or unexpected behaviour with a reproduce-hypothesize-verify loop instead of guessing. Use when something is broken, a test fails, an error or stack trace is reported, or a previous fix did not work.
argument-hint: "<symptom, error or failing test>"
---

# Systematic debugging

Rule: no fix before the root cause is confirmed by evidence.

## Workflow
1. **Reproduce.** Get a command that shows the failure (failing test, script, curl). Record the exact output. If it
   does not reproduce, gather facts (versions, inputs, logs) before anything else.
2. **Read the evidence.** The full error and stack trace, the code at each frame, recent changes
   (`git log -p -5 -- <file>`, `git diff`).
3. **Hypothesize.** Write 1-3 candidate causes, each with a prediction that would confirm or refute it.
4. **Test the hypotheses** cheapest first: add a temporary log/print, run a smaller input, check a value in a REPL,
   bisect (`git bisect` or commenting out halves). One variable at a time.
5. **Fix the cause**, not the symptom, in the smallest change. Remove temporary instrumentation.
6. **Prove it.** Re-run the reproduction (now passing), add a regression test, run the related suite.
7. **Report**: root cause (one sentence + evidence), the fix, how it was verified, related risks.

## When stuck (3 failed attempts)
Stop changing code. Re-read the error from scratch, question an assumption you have not verified (config, version,
environment, the file actually loaded), and widen the search (grep for other writers of the broken value).

## Pitfalls
- Shotgun changes: several edits at once hide which one mattered.
- Catching/suppressing the exception is not a fix.
- "Works on my input" is not verification: re-run the original reproduction.
