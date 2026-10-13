---
name: test-driven-development
description: Implement a feature or fix a bug test-first (red, green, refactor) with the project's own test runner. Use when the user asks for TDD, tests first, a regression test, or when correctness of tricky logic matters.
argument-hint: "<behaviour to implement or bug to fix>"
---

# Test-driven development

## Workflow
1. **Find the test setup.** Look for the runner and conventions: `package.json` scripts (vitest/jest), `pytest.ini` /
   `pyproject.toml`, `go test`, `cargo test`; read 1-2 existing tests and mirror their style and location.
2. **List behaviours.** Write the cases as one-line examples before any code: happy path, edge cases (empty, max,
   unicode, None), error cases. Keep them in the todo list.
3. **Red.** Write ONE failing test for the first behaviour. Run only that test and confirm it fails for the right
   reason (assertion, not import error).
4. **Green.** Write the minimal code that passes it. Run the test.
5. **Refactor.** Clean names and duplication with the test green. Run the file's tests again.
6. Repeat 3-5 for each behaviour. For a bug: the first test reproduces the bug exactly.
7. **Finish.** Run the whole relevant suite (and the linter/typechecker if the project has one). Report the commands
   and results.

## Definition of done
- Every listed behaviour has a test that failed before and passes now.
- The full suite passes; no test skipped or weakened to get green.

## Pitfalls
- Writing all tests then all code: you lose the signal of each red step.
- Tests that mirror the implementation (mocking everything) prove nothing; assert observable behaviour.
- Never edit a failing test to match wrong output.
