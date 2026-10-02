"""Gov task orchestration (PRD P5): scheduled governance checks with
an in-process sweeper (no external scheduler dependency)."""
import os
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.log import logger
from app.services import gov_service

_TASK_DDL = """
CREATE TABLE IF NOT EXISTS data_agent.gov_task (
    task_id VARCHAR(64) NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    enabled BOOLEAN NOT NULL,
    interval_minutes INT NOT NULL,
    last_run_at DATETIME,
    last_status VARCHAR(16),
    last_detail STRING,
    created_at DATETIME
) UNIQUE KEY(task_id)
DISTRIBUTED BY HASH(task_id) BUCKETS 3
PROPERTIES ("replication_num" = "1")
"""

DEFAULT_TASK_ID = "quality_daily"
_DEFAULT_INTERVAL = int(os.getenv("GOV_QUALITY_INTERVAL_MIN", "1440"))

# task_type -> executor; extend as new governance jobs land
_EXECUTORS = {}


def executor(task_type):
    def _wrap(fn):
        _EXECUTORS[task_type] = fn
        return fn
    return _wrap


def is_due(last_run_at, interval_minutes: int, now: datetime) -> bool:
    """Pure scheduling predicate (unit-tested)."""
    if interval_minutes <= 0:
        return True
    if last_run_at is None:
        return True
    if isinstance(last_run_at, str):
        try:
            last_run_at = datetime.fromisoformat(last_run_at)
        except Exception:
            return True
    return now - last_run_at >= timedelta(minutes=interval_minutes)


@executor("quality_check")
async def _run_quality_check(session) -> dict:
    result = await gov_service.run_quality_checks(session)
    return {"checks_total": result.get("checks_total"),
            "checks_passed": result.get("checks_passed"),
            "failures": len(result.get("failures", [])),
            "run_id": result.get("run_id")}


EVAL_SMOKE_N = int(os.getenv("GOV_EVAL_SMOKE_N", "3"))
EVAL_SMOKE_TIMEOUT = int(os.getenv("GOV_EVAL_SMOKE_TIMEOUT", "150"))


@executor("eval_smoke")
async def _run_eval_smoke(session) -> dict:
    """Scheduled smoke eval: N questions through the real langgraph
    pipeline; accuracy read back from eval_runs by run_id."""
    import contextlib
    import io as _io
    from app.eval.config import EvalConfig
    from app.eval.run_eval import run_eval as _run_eval
    run_id = "gov_" + datetime.now().strftime("%Y%m%d%H%M%S")
    # Freeze "today" to the data freeze date (max dt in the fact table):
    # gold SQL in the question set was authored against frozen data, so
    # relative date windows must resolve to the same era to be comparable.
    mock_date = os.getenv("GOV_EVAL_MOCK_DATE", "")
    if not mock_date:
        row = (await session.execute(text(
            "SELECT MAX(dt) FROM dw.ads_gmv_total_day"))).fetchone()
        mock_date = str(row[0]) if row and row[0] else ""
    cfg = EvalConfig(
        question_limit=EVAL_SMOKE_N,
        timeout_sec=EVAL_SMOKE_TIMEOUT,
        run_id=run_id,
        model_name=os.getenv("LLM_MODEL_NAME", "GLM-5.3"),
        mock_date=mock_date or None,
    )
    # run_eval prints a unicode report; the Windows GBK console would
    # raise inside print() - swallow stdout, results live in eval_runs.
    with contextlib.redirect_stdout(_io.StringIO()):
        await _run_eval(cfg, "langgraph", limit=EVAL_SMOKE_N)
    r = (await session.execute(text(
        "SELECT COUNT(*), SUM(level3_ex) FROM data_agent.eval_runs "
        "WHERE run_id = :rid"), {"rid": run_id})).fetchone()
    total, passed = int(r[0] or 0), int(r[1] or 0)
    # Flywheel closure: EX-passed pairs enter the exemplar store with the
    # canonical gold SQL (verified answers -> few-shot context).
    exemplars_added = 0
    try:
        from app.services import exemplar_store
        rows = (await session.execute(text(
            "SELECT q.question, q.gold_sql FROM data_agent.eval_runs e "
            "JOIN data_agent.eval_questions q ON e.question_id = q.question_id "
            "WHERE e.run_id = :rid AND e.level3_ex = 1"),
            {"rid": run_id})).fetchall()
        for question, gold in rows:
            if await exemplar_store.add_exemplar(
                    session, question, gold, "eval_pass", run_id):
                exemplars_added += 1
    except Exception as ex_err:
        logger.warning(f"eval->exemplar flywheel skipped: {ex_err}")
    return {"questions": total, "ex_passed": passed,
            "accuracy_pct": round(passed / total * 100, 1) if total else None,
            "exemplars_added": exemplars_added,
            "run_id": run_id}


async def seed_tasks(session) -> None:
    """Idempotently ensure the default tasks exist."""
    await session.execute(text(_TASK_DDL))
    row = (await session.execute(text(
        "SELECT COUNT(*) FROM data_agent.gov_task WHERE task_id = :tid"),
        {"tid": DEFAULT_TASK_ID})).scalar()
    if not row:
        await session.execute(text(
            "INSERT INTO data_agent.gov_task "
            "(task_id, task_type, enabled, interval_minutes, created_at) "
            "VALUES (:tid, 'quality_check', true, :iv, :ts)"),
            {"tid": DEFAULT_TASK_ID, "iv": _DEFAULT_INTERVAL, "ts": datetime.now()})
        await session.commit()
        logger.info(f"gov_task seeded default {DEFAULT_TASK_ID} (every {_DEFAULT_INTERVAL}min)")
    # eval_weekly seeded DISABLED: it fires real LLM queries (~3min) and
    # must not run automatically right after every restart.
    row2 = (await session.execute(text(
        "SELECT COUNT(*) FROM data_agent.gov_task WHERE task_id = 'eval_weekly'"))).scalar()
    if not row2:
        await session.execute(text(
            "INSERT INTO data_agent.gov_task "
            "(task_id, task_type, enabled, interval_minutes, created_at) "
            "VALUES ('eval_weekly', 'eval_smoke', false, 10080, :ts)"),
            {"ts": datetime.now()})
        await session.commit()
        logger.info("gov_task seeded eval_weekly (disabled; enable via admin)")
    # post_ddl_eval: event-driven row - modeling execute fires it in the
    # background via run_task_now; disabled so the sweeper never picks it.
    row3 = (await session.execute(text(
        "SELECT COUNT(*) FROM data_agent.gov_task WHERE task_id = 'post_ddl_eval'"))).scalar()
    if not row3:
        await session.execute(text(
            "INSERT INTO data_agent.gov_task "
            "(task_id, task_type, enabled, interval_minutes, created_at) "
            "VALUES ('post_ddl_eval', 'eval_smoke', false, 0, :ts)"),
            {"ts": datetime.now()})
        await session.commit()
        logger.info("gov_task seeded post_ddl_eval (event-driven)")


async def list_tasks(session) -> list:
    rows = (await session.execute(text(
        "SELECT task_id, task_type, enabled, interval_minutes, last_run_at, "
        "last_status, last_detail FROM data_agent.gov_task ORDER BY task_id"))).fetchall()
    return [{"task_id": r[0], "task_type": r[1], "enabled": bool(r[2]),
             "interval_minutes": int(r[3] or 0), "last_run_at": str(r[4]) if r[4] else None,
             "last_status": r[5], "last_detail": r[6],
             "due": is_due(r[4], int(r[3] or 0), datetime.now())} for r in rows]


async def create_task(session, task_id: str, task_type: str, interval_minutes: int = 1440) -> dict:
    if task_type not in _EXECUTORS:
        return {"ok": False, "error": f"unknown task_type {task_type!r}; known: {sorted(_EXECUTORS)}"}
    await session.execute(text(
        "INSERT INTO data_agent.gov_task "
        "(task_id, task_type, enabled, interval_minutes, created_at) "
        "VALUES (:tid, :tt, true, :iv, :ts)"),
        {"tid": task_id, "tt": task_type, "iv": max(0, int(interval_minutes)), "ts": datetime.now()})
    await session.commit()
    return {"ok": True, "task_id": task_id}


async def toggle_task(session, task_id: str) -> dict:
    row = (await session.execute(text(
        "SELECT enabled FROM data_agent.gov_task WHERE task_id = :tid"),
        {"tid": task_id})).fetchone()
    if row is None:
        return {"ok": False, "error": f"task {task_id!r} not found"}
    new_val = not bool(row[0])
    await session.execute(text(
        "UPDATE data_agent.gov_task SET enabled = :en WHERE task_id = :tid"),
        {"en": new_val, "tid": task_id})
    await session.commit()
    return {"ok": True, "task_id": task_id, "enabled": new_val}


async def _execute_and_record(session, row) -> dict:
    task_id, task_type = row[0], row[1]
    fn = _EXECUTORS.get(task_type)
    if fn is None:
        return {"task_id": task_id, "status": "error", "detail": f"no executor for {task_type!r}"}
    try:
        detail = await fn(session)
        await session.execute(text(
            "UPDATE data_agent.gov_task SET last_run_at = :ts, last_status = 'success', "
            "last_detail = :d WHERE task_id = :tid"),
            {"ts": datetime.now(), "d": str(detail), "tid": task_id})
        await session.commit()
        return {"task_id": task_id, "status": "success", "detail": detail}
    except Exception as e:
        await session.rollback()
        try:
            await session.execute(text(
                "UPDATE data_agent.gov_task SET last_run_at = :ts, last_status = 'error', "
                "last_detail = :d WHERE task_id = :tid"),
                {"ts": datetime.now(), "d": str(e)[:500], "tid": task_id})
            await session.commit()
        except Exception:
            await session.rollback()
        return {"task_id": task_id, "status": "error", "detail": str(e)[:200]}


async def run_task_now(session, task_id: str) -> dict:
    row = (await session.execute(text(
        "SELECT task_id, task_type, enabled, interval_minutes FROM data_agent.gov_task "
        "WHERE task_id = :tid"), {"tid": task_id})).fetchone()
    if row is None:
        return {"ok": False, "error": f"task {task_id!r} not found"}
    return {"ok": True, **await _execute_and_record(session, row)}


async def run_due_tasks(session) -> list:
    """Sweep entry point: run every enabled task whose interval elapsed."""
    rows = (await session.execute(text(
        "SELECT task_id, task_type, enabled, interval_minutes, last_run_at "
        "FROM data_agent.gov_task WHERE enabled = true"))).fetchall()
    now = datetime.now()
    ran = []
    for row in rows:
        if not is_due(row[4], int(row[3] or 0), now):
            continue
        result = await _execute_and_record(session, row)
        logger.info(f"gov_task {result['task_id']}: {result['status']}")
        ran.append(result)
    return ran
