# Code Review

## What to Check For

### Correctness
- Does the code do what it's supposed to do?
- Are edge cases handled (empty input, nil/null, error paths)?
- Are there off-by-one errors or incorrect conditionals?

### Security
- No secrets or credentials hardcoded in source
- Inputs are validated before use
- No path traversal vulnerabilities (when handling file paths)
- Dependencies are pinned to specific versions

### Performance
- No unnecessary loops or repeated work inside hot paths
- Database queries are bounded (use LIMIT, pagination)
- Large allocations avoided in tight loops

### Maintainability
- Functions do one thing (single responsibility)
- Variable and function names are self-documenting
- Complex logic has a brief comment explaining *why*, not *what*
- Dead code is removed, not commented out

### Consistency
- Matches existing patterns and conventions in the codebase
- Uses the same logging, error handling, and naming style as surrounding code

## Comment Style
When reviewing:
- Be specific: quote the exact line or block
- Explain *why* something is problematic, not just that it is
- Suggest a concrete fix when possible
- Distinguish blocking issues from optional improvements

## What NOT to Flag
- Formatting/whitespace (handled by linters/formatters)
- Personal style preferences when both styles are equally valid
- Changes that are correct but could be marginally cleaner
