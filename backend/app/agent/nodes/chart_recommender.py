"""Automatic chart selection node: LLM recommends the best chart type based on data characteristics

Chart types:
  bar       bar chart (category comparison)
  line      line chart (time trend)
  pie       pie chart (proportion distribution, <=6 categories)
  scatter   scatter plot (correlation)
  card      card (single value)
  table     table (multi-dimensional cross)
"""
import json
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger


def _detect_chart_heuristic(data: list) -> str:
    if not data:
        return "empty"
    """Rule engine: quickly pre-judge chart type (without calling LLM)"""
    if not data:
        return "table"
    cols = list(data[0].keys()) if isinstance(data[0], dict) else []
    time_keywords = ["date", "month", "day", "year", "quarter", "time"]
    def _is_time_col(name):
        return any(kw in str(name).lower() for kw in time_keywords)
    numeric_cols = [c for c in cols if _is_numeric(data[0].get(c)) and not _is_time_col(c)]
    category_cols = [c for c in cols if not _is_numeric(data[0].get(c)) or _is_time_col(c)]

    if len(data) == 1 and len(cols) == 1:
        return "card"
    if len(category_cols) >= 1 and len(numeric_cols) >= 1:
        # Time column detection
        has_time = any(_is_time_col(c) for c in cols)
        if has_time and len(data) >= 3:
            return "line"
        if len(data) <= 6 and len(numeric_cols) == 1:
            return "pie"
        if len(numeric_cols) >= 2 and len(data) >= 3:
            return "scatter"
        return "bar"
    return "table"


def _is_numeric(val) -> bool:
    """Determine if a value is numeric (including string numerics, Vanna #763 fix)"""
    if val is None:
        return False
    # string numeric: "123" / "45.67" / "1,234.56"
    if isinstance(val, str):
        cleaned = val.replace(",", "").replace("%", "").replace(" ", "").strip()
        if not cleaned:
            return False
        try:
            float(cleaned)
            return True
        except ValueError:
            return False
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


async def chart_recommender(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """LLM + rule hybrid recommendation for chart type"""
    writer = runtime.stream_writer
    writer({"stage": "Chart Recommendation"})
    try:
        data = state.get("_last_result", [])
        query = state.get("query", "")

        # 1. Rule pre-judgment (fast, no LLM cost)
        heuristic = _detect_chart_heuristic(data)

        # 2. LLM precise recommendation (only called when data is complex)
        chart_type = heuristic
        chart_config = {}

        if data and len(data) >= 2:
            cols = list(data[0].keys()) if isinstance(data[0], dict) else []
            sample = json.dumps(data[:3], ensure_ascii=False, default=str)

            prompt = f"""Based on the user question and data characteristics, recommend the best visualization chart type.

User question: {query[:100]}
Data columns: {cols}
Data sample: {sample}

Available types: bar (bar), line (line), pie (pie), scatter (scatter), card (card), table (table)

Output JSON only:
{{"type": "chart type", "x_axis": "X-axis column name", "y_axis": "Y-axis column name", "reason": "one-sentence reason"}}"""

            try:
                resp = await llm.ainvoke([HumanMessage(content=prompt)])
                content = resp.content.strip()
                if "```" in content:
                    parts = content.split("```")
                    content = parts[1] if len(parts) > 1 else parts[0]
                    if content.startswith("json\n"):
                        content = content[5:]
                rec = json.loads(content)
                chart_type = rec.get("type", heuristic)
                chart_config = {
                    "x_axis": rec.get("x_axis", ""),
                    "y_axis": rec.get("y_axis", ""),
                    "reason": rec.get("reason", ""),
                }
            except Exception as e:
                logger.warning(f"LLM chart recommendation failed, using rule pre-judgment: {e}")

        # 3. Output recommendation result
        result = {
            "chart_type": chart_type,
            "chart_config": chart_config,
            "heuristic": heuristic,
        }
        logger.info(f"Chart recommendation: {chart_type} (rule: {heuristic}, config: {chart_config})")
        writer({"chart_recommendation": result})
        return {"chart_recommendation": result}
    except Exception as e:
        logger.error(f"Chart recommendation error: {e}")
        return {"chart_recommendation": {"chart_type": "table"}}
