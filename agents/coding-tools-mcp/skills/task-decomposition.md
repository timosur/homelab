# Task Decomposition

## Core Principle
Do the simplest thing that fully solves the task. Avoid scope creep.

## When to Split a PR
Split into multiple PRs when:
- Changes are independent (can be reviewed and merged separately)
- One change is risky and should be isolated for easy rollback
- The PR would touch more than ~400 lines across unrelated areas

Keep together when:
- The changes are logically inseparable (e.g., rename a function + update all callers)
- The second change only makes sense after the first

## Breaking Down a Complex Task

1. **Understand before acting** — Read the codebase first. Use `search_files`, `list_directory`, `read_file`. Don't guess at file locations or function signatures.

2. **Identify the smallest unit of work** — What is the minimum change that delivers value?

3. **Work from the inside out** — Implement data models / core logic first, then the API layer, then the UI / integration layer.

4. **Verify at each step** — Run tests or linters after each logical unit, not just at the end.

## Task Execution Order
For a typical coding task:
1. Explore and understand existing patterns
2. Implement the core change
3. Update tests (or add tests if none exist and the task implies testing)
4. Update documentation if a public interface changed
5. Run validation (`run_command` with test/lint command)
6. Commit and push

## Stopping Conditions
Stop and report back (via PR description or comment) when:
- The task requires a decision that wasn't specified (e.g., schema design choice)
- A dependency is missing or broken and cannot be fixed within this task's scope
- The task scope is larger than expected — describe what you found and propose a split
