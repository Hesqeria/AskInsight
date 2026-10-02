"""One-shot DDL applier for the P1/P2 tables + clarify migration.

Covers (idempotent, in order):
  - session_event_schema.sql   (M1, dynamic monthly partition)
  - agent_inbox_schema.sql     (M3)
  - result_spill_schema.sql    (M10)
  - clarify_add_request_id.sql (M1 request_id migration; ignored if
                                 the column already exists)

Run from backend/:
    python -m app.scripts.apply_p2_ddl            # apply to data_agent
    python -m app.scripts.apply_p2_ddl --check    # dry-run: parse + print
                                                   # the plan, no DB writes
"""
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.conf.app_config import app_config
from app.core.log import logger

DDL_DIR = Path(__file__).parents[2] / "conf" / "ddl"

DDL_FILES = [
    "session_event_schema.sql",
    "agent_inbox_schema.sql",
    "result_spill_schema.sql",
    "clarify_add_request_id.sql",
]


def _split_statements(sql_text: str) -> list[str]:
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


def print_plan() -> int:
    """--check: parse every DDL file and print the statement plan
    without touching the database."""
    missing = 0
    for fname in DDL_FILES:
        path = DDL_DIR / fname
        if not path.exists():
            print(f"[MISSING] {fname}")
            missing += 1
            continue
        stmts = _split_statements(path.read_text(encoding="utf-8"))
        print(f"[{fname}] -> {len(stmts)} statement(s)")
        for st in stmts:
            head = st.split(chr(10))[0][:90]
            print(f"    - {head}")
    print()
    print("Dry-run only - no SQL was executed.")
    return 1 if missing else 0


async def apply_all():
    cfg = app_config.doris
    url = (f"mysql+asyncmy://{cfg.user}:{cfg.password}@"
           f"{cfg.host}:{cfg.port}/data_agent")
    engine = create_async_engine(url, echo=False)
    applied = skipped = failed = 0

    async with engine.begin() as conn:
        for fname in DDL_FILES:
            path = DDL_DIR / fname
            if not path.exists():
                logger.warning(f"DDL file missing: {path}")
                failed += 1
                continue
            logger.info(f"=== applying {fname} ===")
            for stmt in _split_statements(path.read_text(encoding="utf-8")):
                head = stmt.split("\n")[0][:80]
                try:
                    await conn.execute(text(stmt))
                    applied += 1
                except Exception as e:
                    msg = str(e).lower()
                    if ("already exist" in msg or "duplicate" in msg
                            or "column exists" in msg
                            or "duplicate column" in msg):
                        skipped += 1
                        logger.info(f"  [SKIP] {head} ({e})")
                    else:
                        failed += 1
                        logger.error(f"  [FAIL] {head}")
                        logger.error(f"         {e}")

    await engine.dispose()
    print(f"=== P2 DDL summary: applied={applied} skipped={skipped} "
          f"failed={failed} ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if "--check" in sys.argv or "--dry-run" in sys.argv:
        sys.exit(print_plan())
    sys.exit(asyncio.run(apply_all()))
