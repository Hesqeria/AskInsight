"""One-shot DDL applier for the Phase-4 ontology/lineage/approval/rl tables.

Idempotent where possible (CREATE TABLE IF NOT EXISTS); for the legacy
approval_ticket table whose schema changed, we RENAME it to
approval_ticket_legacy and create the new one fresh (the old one had
0 rows + no live callers, so this is safe).

Run from backend/:
    python -m app.scripts.apply_phase4_ddl
"""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.conf.app_config import app_config
from app.core.log import logger


DDL_DIR = Path(__file__).parents[2] / "conf" / "ddl"

# Statements that need special handling (not in the .sql files).
PRE_DDL = [
    # Back up the legacy approval_ticket (different schema) before
    # applying the new one. IF NOT EXISTS on the rename isn't supported
    # in older Doris, so we swallow the error if the legacy table is
    # already there.
    "RENAME TABLE approval_ticket TO approval_ticket_legacy",
]

# Files to apply in order. Each file is split into statements on
# semicolons; comments are stripped.
DDL_FILES = [
    "ontology_schema.sql",   # 7 tables (CREATE IF NOT EXISTS, ont_* already exists)
    "approval_schema.sql",   # new approval_ticket
    "rl_schema.sql",         # rl_policy_stats + rl_decision_log
]


def _split_statements(sql_text: str) -> list[str]:
    """Split a .sql file into individual statements, stripping comments.
    Naive but works for our DDL (no semicolons inside strings)."""
    out = []
    current = []
    for line in sql_text.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        current.append(line)
        if line.endswith(";"):
            stmt = " ".join(current).rstrip(";").strip()
            if stmt:
                out.append(stmt)
            current = []
    if current:
        out.append(" ".join(current).strip())
    return out


async def apply_all():
    # Build the async URL from app_config (handles password correctly).
    cfg = app_config.doris
    url = f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/data_agent"
    engine = create_async_engine(url, echo=False)

    applied = 0
    skipped = 0
    failed = 0

    async with engine.begin() as conn:
        # --- Pre-DDL: rename legacy approval_ticket if it exists ---
        try:
            await conn.execute(text(
                "SELECT 1 FROM approval_ticket LIMIT 1"
            ))
            legacy_exists = True
        except Exception:
            legacy_exists = False

        if legacy_exists:
            # Check the schema - if it has 'ticket_id' column, it's already
            # the new shape and we shouldn't rename.
            try:
                r = await conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema='data_agent' "
                    "AND table_name='approval_ticket' AND column_name='ticket_id'"
                ))
                has_new_schema = r.scalar() > 0
            except Exception:
                has_new_schema = False

            if has_new_schema:
                logger.info("approval_ticket already has new schema; skip rename")
            else:
                try:
                    await conn.execute(text(
                        "RENAME TABLE approval_ticket TO approval_ticket_legacy"
                    ))
                    logger.info("renamed legacy approval_ticket -> approval_ticket_legacy")
                except Exception as e:
                    logger.warning(f"rename failed (continuing): {e}")

        # --- Apply each DDL file ---
        for fname in DDL_FILES:
            path = DDL_DIR / fname
            if not path.exists():
                logger.warning(f"DDL file missing: {path}")
                failed += 1
                continue
            logger.info(f"=== applying {fname} ===")
            statements = _split_statements(path.read_text(encoding="utf-8"))
            for stmt in statements:
                first_line = stmt.split("\n")[0][:80]
                try:
                    await conn.execute(text(stmt))
                    applied += 1
                except Exception as e:
                    msg = str(e).lower()
                    # "already exists" type errors -> skip silently.
                    if "already exist" in msg or "duplicate" in msg:
                        skipped += 1
                        logger.info(f"  [SKIP] {first_line} ({e})")
                    else:
                        failed += 1
                        logger.error(f"  [FAIL] {first_line}")
                        logger.error(f"         {e}")

    await engine.dispose()
    print()
    print(f"=== DDL apply summary ===")
    print(f"  applied: {applied}")
    print(f"  skipped: {skipped}")
    print(f"  failed:  {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(apply_all()))
