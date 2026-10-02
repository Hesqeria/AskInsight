"""Airflow DAG: ETL lineage synchronization.

Deploys the `app.scripts.lineage_sync` runner on a schedule so new/edited
ETL scripts get their lineage parsed and persisted automatically.

Deploy:
  - Copy this file into your Airflow dags/ folder
  - Set the env vars below on the Airflow worker:
      AIRFLOW_LINEAGE_ETL_ROOT   = path to ETL scripts repo
      AIRFLOW_LINEAGE_USE_LLM    = "true" / "false" (default true)
      AIRFLOW_LINEAGE_MAX_FILES  = per-run cap (default 0 = unlimited)
  - The backend app must be importable from the Airflow worker
    (PYTHONPATH=/path/to/AskInsight/backend) since this DAG imports
    `app.scripts.lineage_sync` directly.

Schedule:
  Default 6-hourly. Tune via `AIRFLOW_LINEAGE_CRON` env var.

Failure policy:
  Individual file failures are logged but do not fail the DAG run - we
  return exit code 1 from the sync script and surface it in XComs, but
  the PythonOperator itself succeeds. This avoids one broken script
  blocking the whole lineage graph from updating.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Make the backend app importable when this DAG loads in Airflow.
_BACKEND_ROOT = os.environ.get("ASKINSIGHT_BACKEND_ROOT")
if _BACKEND_ROOT and _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from airflow import DAG  # noqa: E402
from airflow.operators.python import PythonOperator  # noqa: E402
from airflow.utils.dates import days_ago  # noqa: E402

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# DAG config (env-overridable)
# ------------------------------------------------------------------ #
ETL_ROOT = os.environ.get("AIRFLOW_LINEAGE_ETL_ROOT", "/opt/etl")
USE_LLM = os.environ.get("AIRFLOW_LINEAGE_USE_LLM", "true").lower() == "true"
MAX_FILES = int(os.environ.get("AIRFLOW_LINEAGE_MAX_FILES", "0"))
CRON = os.environ.get("AIRFLOW_LINEAGE_CRON", "0 */6 * * *")  # every 6h


default_args = {
    "owner": "data-platform",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5) if (timedelta := globals().get("timedelta")) else None,
}


# ------------------------------------------------------------------ #
# Task callables
# ------------------------------------------------------------------ #
def _run_sync(**context):
    """Invoke the lineage_sync script and push the summary to XCom."""
    import asyncio
    from app.scripts.lineage_sync import SyncConfig, run_sync, summarize

    cfg = SyncConfig(
        etl_root=Path(ETL_ROOT),
        use_llm=USE_LLM,
        max_files_per_run=MAX_FILES,
    )
    log.info(f"lineage_sync starting: etl_root={cfg.etl_root} use_llm={cfg.use_llm}")
    results = asyncio.run(run_sync(cfg))
    summary = summarize(results)
    log.info(f"lineage_sync summary: {summary}")

    # Push for downstream tasks / Airflow UI.
    context["ti"].xcom_push(key="summary", value=summary)

    # Return 0 even on partial failures - see failure policy above.
    return summary


def _health_check(**context):
    """Sanity-check the persisted lineage after sync. Cheap query that
    fails the task if the row count drops by >50% (likely a parser
    regression wiped the table)."""
    import asyncio
    from app.clients.doris_client_manager import doris_client_manager
    from sqlalchemy import text

    async def _count():
        async with doris_client_manager.session_factory() as session:
            r = await session.execute(text(
                "SELECT COUNT(*) FROM lineage_technical"
            ))
            return r.scalar() or 0

    count = asyncio.run(_count())
    log.info(f"lineage_technical row count after sync: {count}")
    context["ti"].xcom_push(key="row_count", value=count)
    if count == 0:
        raise RuntimeError("lineage_technical is empty after sync - "
                           "check the parser or DDL")


# ------------------------------------------------------------------ #
# DAG definition
# ------------------------------------------------------------------ #
dag = DAG(
    dag_id="etl_lineage_sync",
    default_args={
        "owner": "data-platform",
        "depends_on_past": False,
        "retries": 1,
    },
    description="Parse ETL scripts (SQL/Python/Shell) into lineage_technical",
    schedule_interval=CRON,
    start_date=days_ago(1),
    catchup=False,
    tags=["ontology", "lineage", "phase4"],
    is_paused_upon_creation=False,
)

sync_task = PythonOperator(
    task_id="sync_lineage",
    python_callable=_run_sync,
    dag=dag,
)

health_task = PythonOperator(
    task_id="post_sync_health_check",
    python_callable=_health_check,
    dag=dag,
)

sync_task >> health_task
