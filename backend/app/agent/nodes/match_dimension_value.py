"""Dimension value exact-match node: resolve WHERE values from actual DB data."""
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.keywords import sanitize_keywords
from app.core.log import logger


async def match_dimension_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Match Dimension Values"})
    try:
        value_repo = runtime.context["value_doris_repository"]
        keywords = state.get("keywords", [])
        query = state.get("query", "")

        candidates = sanitize_keywords(keywords + [query])
        matched_values = []
        for kw in candidates:
            if len(kw) < 1:
                continue
            results = await value_repo.search_exact(kw, limit=5)
            for r in results:
                if r:
                    matched_values.append({
                        "value": r.get("value", ""),
                        "column_id": r.get("column_id", ""),
                        "table_name": r.get("table_name", ""),
                    })

        seen = set()
        unique = []
        for v in matched_values:
            key = f"{v.get('column_id', '')}.{v.get('value', '')}"
            if key not in seen:
                seen.add(key)
                unique.append(v)

        logger.info(f"Dimension value match: {len(unique)} matches")
        return {"matched_dimension_values": unique[:30]}
    except Exception as e:
        logger.error(f"Dimension value match error: {e}")
        return {"matched_dimension_values": []}
