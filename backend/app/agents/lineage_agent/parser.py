"""Lineage parser agent (P3-06): parse Python/SQL/Shell ETL code into
column-level technical lineage edges.

Mirrors the recommender/analyzer pattern (LLM-driven with rule-based
fallback). The rule-based baseline for SQL reuses the existing
`extract_lineage_from_sql` regex parser so we don't lose any of its
tuning; LLM is layered on top for higher accuracy and Python support.

Output edges are persisted to the `lineage_technical` table.
"""
import json
import re
from typing import Optional

from app.core.log import logger
from app.ontology import LineageTechnicalEdge


# ------------------------------------------------------------------ #
# JSON extraction (tolerant of ```json fences / leading prose)
# ------------------------------------------------------------------ #
def _extract_json_object(content):
    """Tolerantly extract the first JSON object from an LLM response.
    Delegates to app.core.json_guard.safe_json_parse (single source of
    truth - three agent copies of this existed and drifted)."""
    from app.core.json_guard import safe_json_parse
    return safe_json_parse(content)

# ------------------------------------------------------------------ #
# Transformation classification
# ------------------------------------------------------------------ #
_AGG_FUNCS = {"SUM", "COUNT", "AVG", "MIN", "MAX", "STDDEV", "VAR_SAMP"}
_VALID_TRANSFORMS = {
    "DIRECT", "JOIN", "AGG", "FILTER", "CASE", "UNION", "DERIVED",
}


def _classify_transform(raw: Optional[str]) -> str:
    """Normalize an LLM-provided transform label to one of the 7
    canonical values defined in the PRD §5.1.2."""
    if not raw:
        return "DIRECT"
    t = str(raw).strip().upper()
    if t in _VALID_TRANSFORMS:
        return t
    if t in _AGG_FUNCS:
        return "AGG"
    if "WHERE" in t or "FILTER" in t:
        return "FILTER"
    if "CASE" in t or "WHEN" in t:
        return "CASE"
    if "JOIN" in t:
        return "JOIN"
    if "UNION" in t:
        return "UNION"
    return "DERIVED"


# ------------------------------------------------------------------ #
# Edge normalization
# ------------------------------------------------------------------ #
def _row_to_edge(row: dict, default_db: str = "dw") -> Optional[LineageTechnicalEdge]:
    """Coerce one parsed dict into a LineageTechnicalEdge. Returns None
    if the row is missing required fields."""
    if not isinstance(row, dict):
        return None
    src = row.get("src") or row.get("source") or {}
    dst = row.get("dst") or row.get("target") or {}
    if isinstance(src, str):
        src = _parse_table_column(src, default_db)
    if isinstance(dst, str):
        dst = _parse_table_column(dst, default_db)
    if not src or not dst:
        return None
    src_table = src.get("table")
    dst_table = dst.get("table")
    if not src_table or not dst_table:
        return None

    src_db = src.get("db") or default_db
    dst_db = dst.get("db") or default_db
    transform = _classify_transform(row.get("transform") or row.get("transformation"))
    src_col = src.get("column")
    dst_col = dst.get("column")
    src_full = f"{src_db}.{src_table}.{src_col or '*'}"
    dst_full = f"{dst_db}.{dst_table}.{dst_col or '*'}"
    lineage_id = f"{src_full}->{dst_full}:{transform}"

    return LineageTechnicalEdge(
        lineage_id=lineage_id,
        src_db=src_db, src_table=src_table, src_column=src_col,
        dst_db=dst_db, dst_table=dst_table, dst_column=dst_col,
        transformation=transform,
    )


def _parse_table_column(s: str, default_db: str) -> dict:
    """Parse 'dw.dwd_order.final_amount' / 'dwd_order.final_amount' /
    'final_amount' (bare) into {db, table, column}."""
    if not s:
        return {}
    parts = [p.strip().strip('`"[]') for p in s.split(".")]
    if len(parts) >= 3:
        return {"db": parts[0], "table": parts[1], "column": parts[2]}
    if len(parts) == 2:
        return {"db": default_db, "table": parts[0], "column": parts[1]}
    # Single token - treat as table only.
    return {"db": default_db, "table": parts[0], "column": None}


# ------------------------------------------------------------------ #
# Rule-based SQL fallback (delegates to existing extract_lineage_from_sql)
# ------------------------------------------------------------------ #
def _rule_based_sql(sql: str, default_db: str = "dw") -> list[LineageTechnicalEdge]:
    """Wrap the existing regex SQL parser into the LineageTechnicalEdge shape."""
    if not sql or not sql.strip():
        return []
    try:
        from app.agent.nodes.extract_lineage import extract_lineage_from_sql
    except Exception:
        return []
    raw_rows = extract_lineage_from_sql(sql)
    edges = []
    for r in raw_rows:
        src_table = r.get("source_table") or ""
        dst_table = "_query_result_"   # SELECT lineage has no real dst table
        if not src_table:
            continue
        edges.append(LineageTechnicalEdge(
            lineage_id=f"{default_db}.{src_table}.{r.get('source_column','*')}->{dst_table}.{r.get('target_column','*')}:{r.get('transformation','DIRECT')}",
            src_db=default_db, src_table=src_table,
            src_column=r.get("source_column"),
            dst_db=default_db, dst_table=dst_table,
            dst_column=r.get("target_column"),
            transformation=_classify_transform(r.get("transformation")),
        ))
    return edges


# ------------------------------------------------------------------ #
# LineageParser
# ------------------------------------------------------------------ #
_LLM_TIMEOUT_S = 15.0


class LineageParser:
    """LLM-driven ETL lineage parser with rule-based fallback.

    The LLM is *optional* - when None, only the rule-based SQL fallback
    runs (and Python parsing is a no-op). This keeps the parser usable
    on dev boxes without an LLM key.
    """

    def __init__(self, llm=None, default_db: str = "dw"):
        # `llm` is expected to expose `.complete(messages, task_type=...)`
        # matching the LLMRouter interface (consistent with recommender/analyzer).
        self._llm = llm
        self.default_db = default_db

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def parse_sql(self, sql: str, use_llm: bool = True) -> list[LineageTechnicalEdge]:
        """Parse a SQL script. Always runs the rule-based baseline; if
        `use_llm` is True and an LLM is wired, the LLM result replaces
        the baseline (it's strictly more accurate when available)."""
        if not sql or not sql.strip():
            return []
        baseline = _rule_based_sql(sql, self.default_db)
        if not use_llm or self._llm is None:
            return baseline
        try:
            import asyncio
            llm_edges = await asyncio.wait_for(
                self._parse_via_llm(sql, "sql"), timeout=_LLM_TIMEOUT_S,
            )
            # Prefer LLM edges when present; fall back to baseline.
            return llm_edges if llm_edges else baseline
        except Exception as e:
            logger.warning(
                f"Lineage LLM parse failed, using rule-based: {e}"
            )
            return baseline

    async def parse_python(self, code: str) -> list[LineageTechnicalEdge]:
        """Parse a Python ETL script. No rule-based fallback exists for
        Python - returns [] when LLM is unavailable."""
        if not code or not code.strip() or self._llm is None:
            return []
        try:
            import asyncio
            return await asyncio.wait_for(
                self._parse_via_llm(code, "python"), timeout=_LLM_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(f"Python lineage LLM parse failed: {e}")
            return []

    async def parse(self, code: str, code_type: str = "sql") -> list[LineageTechnicalEdge]:
        """Dispatch by code type. `code_type` in {sql, python, shell}."""
        code_type = (code_type or "sql").lower()
        if code_type == "sql":
            return await self.parse_sql(code)
        if code_type in ("python", "py"):
            return await self.parse_python(code)
        # Shell / other: try LLM with generic prompt (no rule baseline).
        if self._llm is None:
            return []
        try:
            import asyncio
            return await asyncio.wait_for(
                self._parse_via_llm(code, code_type), timeout=_LLM_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(f"{code_type} lineage LLM parse failed: {e}")
            return []

    # ------------------------------------------------------------------ #
    # LLM call
    # ------------------------------------------------------------------ #
    async def _parse_via_llm(self, code: str, code_type: str) -> list[LineageTechnicalEdge]:
        import asyncio
        def _call():
            prompt = self._build_prompt(code, code_type)
            resp = self._llm.complete(
                [{"role": "user", "content": prompt}],
                task_type="sql_complex", temperature=0, max_tokens=2048,
            )
            return resp.get("content") or ""
        content = await asyncio.to_thread(_call)
        return self._parse_response(content)

    def _build_prompt(self, code: str, code_type: str) -> str:
        NL = chr(10)
        return (
            "你是数据血缘解析专家。给定一段 " + code_type + " ETL 代码,提取列级数据血缘。" + NL +
            "只输出 JSON,不要解释。格式如下:" + NL +
            "{" + NL +
            '  "edges": [' + NL +
            '    {"src": "db.table.column", "dst": "db.table.column", "transform": "DIRECT|JOIN|AGG|FILTER|CASE|UNION|DERIVED"}' + NL +
            "  ]," + NL +
            '  "confidence": 0.0-1.0' + NL +
            "}" + NL +
            "规则:" + NL +
            "- src/dst 必须是完整路径 (db.table.column);若代码中只有 table.column,默认 db=" + self.default_db + NL +
            "- transform 用 DIRECT(直接拷贝)/JOIN/AGG(SUM/COUNT/AVG)/FILTER(WHERE)/CASE/UNION/DERIVED(表达式派生)" + NL +
            "- 列级粒度优先;无法判断列时填 null" + NL +
            "- 没有血缘时返回 {\"edges\": [], \"confidence\": 1.0}" + NL +
            NL +
            "代码:" + NL + code
        )

    def _parse_response(self, content: str) -> list[LineageTechnicalEdge]:
        data = _extract_json_object(content)
        if data is None:
            return []
        edges_raw = data.get("edges") or []
        if not isinstance(edges_raw, list):
            return []
        out = []
        for row in edges_raw:
            edge = _row_to_edge(row, default_db=self.default_db)
            if edge is not None:
                out.append(edge)
        return out
