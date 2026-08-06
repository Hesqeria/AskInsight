# Contributing Guide

Thanks for your interest in the intelligent data-query project! Issues, PRs and suggestions are all welcome.

## Development Environment

```bash
git clone https://github.com/your-org/askinsight.git
cd intelligent-decision-analytics

# Backend
cd backend && pip install -e . && cd ..

# Frontend
cd frontend && npm install && cd ..

# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

## Commit Convention

```
<type>: <description>

Types:
  feat     New feature
  fix      Bug fix
  docs     Documentation
  refactor Refactor
  test     Tests
  chore    Misc
```

## Branching Strategy

- `main` - Stable release
- `develop` - Development integration
- `feature/*` - Feature branches
- `fix/*` - Fix branches

## Code Standards

- Python: `ruff check .` must pass fully
- Frontend: `npm run build` must have no errors
- New features must include tests

## PR Workflow

1. Fork → create branch → commit → PR
2. CI must be green (lint + test)
3. At least one Reviewer approval
4. Squash merge to main
