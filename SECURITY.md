# Security Policy

## Credential Management

**Accounts, passwords, API keys, and internal IPs MUST NOT be committed to this repository.**

### What is considered sensitive

- Database passwords (Doris, PostgreSQL, MySQL)
- Milvus credentials
- Redis passwords
- Superset credentials
- LLM API keys (DeepSeek, OpenAI, etc.)
- Embedding service API keys
- JWT secrets
- Internal IP addresses (192.168.x.x)
- Kafka credentials

### How to configure locally

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Fill in your actual credentials in `.env`

3. The `.env` file is in `.gitignore` — it will never be committed

### How the app reads config

All credentials are loaded via environment variables:

```yaml
# app_config.yaml (safe to commit)
password: ${oc.env:DORIS_PASSWORD,}  # empty default, must be set in .env
```

### Pre-commit checklist

Before committing, verify no secrets are present:

```bash
# Check for passwords
grep -rn "nld1024\|StrongAttu\|StrongRedis\|Strongnld\|StrongPg" . --include="*.py" --include="*.yaml" | grep -v node_modules

# Check for internal IPs
grep -rn "192\.168\." . --include="*.py" --include="*.yaml" | grep -v node_modules
```

Both commands should return empty.

### If you accidentally committed secrets

1. Do NOT push
2. `git commit --amend` or `git reset HEAD~1` to rewrite locally
3. If already pushed, rotate all compromised credentials immediately
