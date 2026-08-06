"""SQLite persistence for pipeline runs with node-level granularity."""

import sqlite3
import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path


class PipelinePersistence:
    """Stores pipeline runs and node runs in SQLite. Supports save/resume/cache."""

    def __init__(self, db_path: str = "data/pipeline_runs.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    pipeline_name TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'INIT',
                    params TEXT,
                    share_data TEXT,
                    outputs TEXT,
                    error TEXT,
                    started_at TEXT,
                    ended_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS node_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    node_type TEXT,
                    input_hash TEXT,
                    output TEXT,
                    state TEXT NOT NULL DEFAULT 'INIT',
                    error TEXT,
                    retry_count INTEGER DEFAULT 0,
                    started_at TEXT,
                    ended_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_node_runs_hash ON node_runs (node_name, input_hash, state)"
            )
        conn.close()

    def save_run(self, ctx, state: str, error: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pipeline_runs
                   (run_id, pipeline_name, state, params, share_data, outputs, error, started_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ctx.run_id,
                    ctx.pipeline_name,
                    state,
                    json.dumps(ctx.share_data.get("_inputs", {})),
                    json.dumps(ctx.share_data),
                    json.dumps(ctx.node_outputs),
                    error,
                    now,
                    now if state in ("DONE", "FAILED") else None,
                ),
            )
        conn.close()

    def save_node_run(self, run_id: str, node_name: str, node_type: str, input_data: Any, output: Any, state: str, error: str = "", retry_count: int = 0):
        now = datetime.now(timezone.utc).isoformat()
        input_hash = self._hash(input_data)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO node_runs
                   (run_id, node_name, node_type, input_hash, output, state, error, retry_count, started_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, node_name, node_type, input_hash, json.dumps(output), state, error, retry_count, now, now),
            )
        conn.close()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT run_id, pipeline_name, state, params, share_data, outputs, error, started_at, ended_at FROM pipeline_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "run_id": row[0], "pipeline_name": row[1], "state": row[2],
            "params": json.loads(row[3] or "{}"),
            "share_data": json.loads(row[4] or "{}"),
            "outputs": json.loads(row[5] or "{}"),
            "error": row[6], "started_at": row[7], "ended_at": row[8],
        }

    def is_cached(self, node_name: str, input_data: Any) -> bool:
        ih = self._hash(input_data)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM node_runs WHERE node_name = ? AND input_hash = ? AND state = 'DONE' LIMIT 1",
                (node_name, ih),
            ).fetchone()
        conn.close()
        return row is not None

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT run_id, pipeline_name, state, error, started_at, ended_at FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [
            {"run_id": r[0], "pipeline_name": r[1], "state": r[2], "error": r[3], "started_at": r[4], "ended_at": r[5]}
            for r in rows
        ]

    @staticmethod
    def _hash(data: Any) -> str:
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()
