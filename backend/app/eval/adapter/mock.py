"""Mock Adapter：直接返回 gold SQL，用于框架测试

价值：
1. 验证评测流水线本身（loader → evaluator → reporter）正确性
2. 应当 100% 通过 EX
3. 框架级 smoke test
"""
import time
from .base import BaseAgentAdapter, AgentResponse


class MockGoldAdapter(BaseAgentAdapter):
    """返回 gold SQL 的 Mock（应得 100% EX）"""

    def __init__(self):
        self._gold_map: dict[str, str] = {}

    def set_gold(self, question_id: str, gold_sql: str):
        self._gold_map[question_id] = gold_sql

    async def ask(self, question: str, question_id: str = "") -> AgentResponse:
        t0 = time.time()
        sql = self._gold_map.get(question_id, "")
        return AgentResponse(
            pred_sql=sql,
            intent="query",
            latency_ms=int((time.time() - t0) * 1000),
            raw_output="[MOCK] returned gold sql",
        )


class MockNoiseAdapter(BaseAgentAdapter):
    """返回带 markdown 包装的 gold SQL，测试 _strip_markdown_fence"""

    def __init__(self):
        self._gold_map: dict[str, str] = {}

    def set_gold(self, question_id: str, gold_sql: str):
        self._gold_map[question_id] = gold_sql

    async def ask(self, question: str, question_id: str = "") -> AgentResponse:
        sql = self._gold_map.get(question_id, "")
        wrapped = f"```sql\n{sql}\n```"
        return AgentResponse(
            pred_sql=wrapped,
            intent="query",
            latency_ms=5,
            raw_output="[MOCK-NOISE] markdown wrapped",
        )


class MockFailureAdapter(BaseAgentAdapter):
    """故意返回错误 SQL，应当 0% EX"""

    async def ask(self, question: str, question_id: str = "") -> AgentResponse:
        return AgentResponse(
            pred_sql="SELECT * FROM nonexistent_table",
            intent="query",
            latency_ms=10,
            error="table not found",
        )


class MockEquivalentAdapter(BaseAgentAdapter):
    """输出与 gold 语义等价但写法不同的 SQL

    用于测试 L4 LLM Judge：L3 应失败（结果集不同），L4 应通过（语义等价）
    """

    # 改写规则：把 LIMIT N 改为 LIMIT N OFFSET 0（等价）
    @staticmethod
    def _rewrite(sql: str) -> str:
        import re
        # 把 SUM(x) AS alias 改成 SUM(x) AS alias2（别名改了，结果集不同，但语义等价）
        new = re.sub(r"AS\s+(\w+)", r"AS \1_v2", sql, count=1)
        return new or sql

    def __init__(self):
        self._gold_map: dict[str, str] = {}

    def set_gold(self, question_id: str, gold_sql: str):
        self._gold_map[question_id] = self._rewrite(gold_sql)

    async def ask(self, question: str, question_id: str = "") -> AgentResponse:
        sql = self._gold_map.get(question_id, "")
        return AgentResponse(
            pred_sql=sql,
            intent="query",
            latency_ms=8,
            raw_output="[MOCK-EQUIV] rewritten alias",
        )
