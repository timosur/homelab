# PR Workflow

## Branch Naming
Create feature branches with descriptive names:
- `feat/<short-description>` — new features
- `fix/<short-description>` — bug fixes
- `refactor/<short-description>` — refactoring without behavior change
- `chore/<short-description>` — tooling, dependencies, config

Always create a branch before committing. Never push directly to `main`.

```
git checkout -b feat/my-feature
```

## Commit Messages
Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body]

[optional footer(s)]
```

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `ci`

Examples:
- `feat(auth): add JWT refresh token support`
- `fix(api): handle empty response from Ollama`
- `chore(deps): bump fastmcp to 2.1.0`

Keep the summary line under 72 characters. Use present tense ("add" not "added").

## PR Description
When calling `gh_pr_create`, use this body structure:

```markdown
## Summary
Brief description of what this PR does and why.

## Changes
- List of key changes made

## Testing
How to verify this works (commands, endpoints, etc.)
```

## PR Checklist (before creating)
1. Run existing tests with `run_command`
2. Check `git_diff` to confirm only intended changes are staged
3. Use a focused, single-purpose PR — one concern per PR
4. Reference related issues if applicable: `Closes #123`
