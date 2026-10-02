"""LiveSQLBench GT 整合脚本测试.

验证:
  - normalize_sol_sql 兼容 str/list/list[dict]
  - integrate 按 instance_id 合入受保护字段
  - 备份 + 写回逻辑
  - loader 读取整合后的 sol_sql
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from app.eval.integrate_gt_data import (
    integrate, normalize_sol_sql, load_jsonl, save_jsonl,
)
from app.eval.dataset.livesqlbench_loader import LiveSQLBenchLoader
from app.eval.run_livesqlbench import _get_sol_candidates


class TestNormalizeSolSql:
    def test_str(self):
        assert normalize_sol_sql("SELECT 1") == ["SELECT 1"]

    def test_empty_str(self):
        assert normalize_sol_sql("") == []

    def test_list_str(self):
        assert normalize_sol_sql(["SELECT 1", "SELECT 2"]) == ["SELECT 1", "SELECT 2"]

    def test_list_dict(self):
        assert normalize_sol_sql([{"sql": "SELECT 1"}]) == ["SELECT 1"]

    def test_list_mixed(self):
        out = normalize_sol_sql(["SELECT 1", {"sql": "SELECT 2"}, "", None])
        assert out == ["SELECT 1", "SELECT 2"]

    def test_none(self):
        assert normalize_sol_sql(None) == []


class TestIntegrate:
    def _public(self):
        return [
            {"instance_id": "a1", "query": "q1", "sol_sql": []},
            {"instance_id": "a2", "query": "q2", "sol_sql": []},
        ]

    def _gt(self):
        return [
            {"instance_id": "a1", "sol_sql": ["SELECT 1"],
             "test_cases": {"type": "ex"}, "external_knowledge": [{"name": "x"}]},
        ]

    def test_matches_and_fills(self):
        out, stats = integrate(self._public(), self._gt())
        assert stats["matched"] == 1
        assert stats["missing"] == 1
        assert out[0]["sol_sql"] == ["SELECT 1"]
        assert out[0]["test_cases"] == {"type": "ex"}
        assert out[0]["external_knowledge"] == [{"name": "x"}]
        # 未匹配的保持原样
        assert out[1]["sol_sql"] == []

    def test_gt_sol_as_str(self):
        gt = [{"instance_id": "a1", "sol_sql": "SELECT 1"}]
        out, stats = integrate(self._public(), gt)
        # str 被规范为 list
        assert out[0]["sol_sql"] == ["SELECT 1"]

    def test_no_gt(self):
        out, stats = integrate(self._public(), [])
        assert stats["matched"] == 0
        assert stats["sol_filled"] == 0
        assert len(out) == 2


class TestEndToEnd:
    def test_full_flow(self, tmp_path):
        """构造公开数据 + GT,整合,loader 读取."""
        # 公开数据
        pub = [
            {"instance_id": "db1_t1", "selected_database": "db1",
             "query": "count?", "category": "Query", "difficulty_tier": "Simple",
             "sol_sql": []},
        ]
        pub_file = tmp_path / "livesqlbench_data.jsonl"
        with open(pub_file, "w", encoding="utf-8") as f:
            for p in pub:
                f.write(json.dumps(p) + "\n")

        # SQLite 库
        db_dir = tmp_path / "databases" / "db1"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "db1_template.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (v INT)")
        conn.execute("INSERT INTO x VALUES (1),(2)")
        conn.commit()
        conn.close()

        # GT
        gt_file = tmp_path / "gt.jsonl"
        with open(gt_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "instance_id": "db1_t1",
                "sol_sql": ["SELECT COUNT(*) FROM x"],
                "test_cases": {"type": "ex_base"},
                "external_knowledge": [],
            }) + "\n")

        # 整合
        from app.eval.integrate_gt_data import main as _main  # noqa
        public = load_jsonl(pub_file)
        gt = load_jsonl(gt_file)
        out, stats = integrate(public, gt)
        assert stats["sol_filled"] == 1
        save_jsonl(out, pub_file)

        # loader 读取
        loader = LiveSQLBenchLoader(tmp_path)
        tasks = loader.load_tasks()
        assert len(tasks) == 1
        assert tasks[0].sol_sql == "SELECT COUNT(*) FROM x"
        cands = _get_sol_candidates(tasks[0])
        assert cands == ["SELECT COUNT(*) FROM x"]

        # 实际评测 mock 应通过
        from app.eval.evaluator.sqlite_ex import SqliteBackend, evaluate_sqlite
        db_file = loader.find_db_file("db1")
        assert db_file is not None
        with SqliteBackend(db_file) as b:
            v = evaluate_sqlite(
                b, instance_id="db1_t1", question="q",
                pred_sql="SELECT COUNT(*) FROM x", sol_sql=cands[0],
            )
            assert v.ex_correct is True
