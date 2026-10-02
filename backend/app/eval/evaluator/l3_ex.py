"""L2 + L3 评测器：SQL 可执行性 + 执行准确率 EX

依赖 result_match 工具
"""
import time
import logging
from dataclasses import dataclass, asdict
from typing import Any

import pymysql

from .result_match import match as match_result

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """单题评测结果"""
    question_id: str
    level1_intent: bool | None = None     # L1 由调用方填
    level2_executable: bool = False
    level3_ex: bool = False
    level4_llm_judge: bool = False        # L4 由调用方在 L3 失败时调用 LLM
    level4_confidence: float = 0.0
    level4_reason: str = ""
    final_pass: bool = False              # L3 OR L4
    pred_sql: str = ""
    pred_result: list = None
    gold_rows: int = 0
    pred_rows: int = 0
    error_msg: str = ""
    latency_ms: int = 0
    match_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # pred_result 不入库（太大）
        d.pop("pred_result", None)
        return d

    def recompute_final(self):
        """L3 失败时，根据 L4 重新计算 final_pass"""
        self.final_pass = self.level3_ex or self.level4_llm_judge


class EXEvaluator:
    """执行准确率评测器

    使用方式：
        ev = EXEvaluator(doris_config)
        result = ev.evaluate(question_id="EQ001", gold_sql="...", pred_sql="...")
    """

    def __init__(self, doris_config: dict):
        self.cfg = doris_config
        # data db 用于执行 SQL（不修改）
        self._conn = pymysql.connect(
            host=self.cfg["host"],
            port=self.cfg["port"],
            user=self.cfg["user"],
            password=self.cfg["password"],
            database=self.cfg["data_db"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.Cursor,
        )

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def _execute(self, sql: str, max_rows: int = 5000) -> tuple[list, str]:
        """在数据快照库执行 SQL，返回 (结果集, 错误信息)"""
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchmany(max_rows)
            return list(rows), ""
        except Exception as e:
            return [], str(e)[:500]

    def evaluate(
        self,
        question_id: str,
        gold_sql: str,
        pred_sql: str,
    ) -> EvalResult:
        """评测单题

        步骤：
        1. 执行 gold_sql（金标准应当已经验证过可执行）
        2. 执行 pred_sql（候选 SQL）
        3. L2 可执行性 = pred_sql 是否无错误
        4. L3 EX = 结果集是否匹配
        """
        result = EvalResult(question_id=question_id)
        result.pred_sql = pred_sql
        t0 = time.time()

        # 1. 执行金标准
        gold, gold_err = self._execute(gold_sql)
        if gold_err:
            result.error_msg = f"gold_sql 错误: {gold_err}"
            result.latency_ms = int((time.time() - t0) * 1000)
            return result
        result.gold_rows = len(gold)

        # 2. 执行候选 SQL
        if not pred_sql or not pred_sql.strip():
            result.error_msg = "候选 SQL 为空"
            result.latency_ms = int((time.time() - t0) * 1000)
            return result

        # 清理 LLM 常见包装（```sql ... ```）
        pred_sql_clean = self._strip_markdown_fence(pred_sql)
        pred, pred_err = self._execute(pred_sql_clean)
        result.latency_ms = int((time.time() - t0) * 1000)

        if pred_err:
            result.error_msg = pred_err
            return result

        result.level2_executable = True
        result.pred_rows = len(pred)
        result.pred_result = pred

        # 3. 结果集匹配
        m = match_result(gold, pred, rtol=self.cfg.get("float_rtol", 1e-6))
        result.level3_ex = m.final_pass
        result.match_reason = m.reason
        result.final_pass = m.final_pass  # 默认 = L3

        return result

    @staticmethod
    def _strip_markdown_fence(sql: str) -> str:
        """去掉 LLM 输出常见的 ```sql ... ``` 包装"""
        s = sql.strip()
        if s.startswith("```"):
            lines = s.split("\n")
            # 去首行（```sql）和尾行（```）
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            s = "\n".join(lines)
        return s.strip()
