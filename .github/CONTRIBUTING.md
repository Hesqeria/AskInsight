# Contributing to AskInsight

## Language Policy

- **All code, comments, docstrings, and commit messages MUST be in English.**
- No Chinese (or any non-English) characters in `backend/app/`, `frontend/src/`, or commit messages.
- Multi-language documentation (CN/JP) lives only in `docs/README_*.md` and root `README.md`.

## Commit Style

```
type(scope): Brief description in English

- Bullet points in English
- No emojis in commit messages
```

## Code Style

- Python: PEP 8, type hints required
- Vue 3: Composition API preferred, SCSS scoped
- No hardcoded secrets — use environment variables

## PR Checklist

- [ ] `rg '[\u4e00-\u9fff]' backend/app/ frontend/src/` returns empty
- [ ] All tests pass
- [ ] Pre-commit hooks pass
