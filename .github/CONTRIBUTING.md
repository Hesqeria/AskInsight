# Contributing to AskInsight

## Security: No secrets in code

**Accounts, passwords, API keys, and internal IPs MUST NOT be committed.**

See [SECURITY.md](../SECURITY.md) for details. Quick check before every commit:

```bash
grep -rn "nld1024\|Strong\|192\.168\." backend/app/ backend/conf/ --include="*.py" --include="*.yaml"
```

Must return empty.

## Language Policy

- All code, comments, docstrings, and commit messages in **English**
- No Chinese characters in `backend/app/`, `frontend/src/`

## Commit Style

```
type(scope): Brief description in English
```

## Code Style

- Python: PEP 8, type hints required
- Vue 3: Composition API, SCSS scoped

## PR Checklist

- [ ] No secrets exposed (passwords, keys, IPs)
- [ ] `rg '[\u4e00-\u9fff]' backend/app/ frontend/src/` returns empty
- [ ] All tests pass
