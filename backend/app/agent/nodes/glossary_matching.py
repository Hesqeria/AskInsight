"""Glossary matching node: business terms -> standard field mapping."""
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.keywords import sanitize_keywords
from app.core.log import logger
from sqlalchemy import text


async def glossary_matching(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Glossary Matching"})
    try:
        meta_repo = runtime.context["meta_doris_repository"]
        query = state.get("query", "")
        keywords = state.get("keywords", [])

        terms = sanitize_keywords(keywords + [query])
        matched = []
        for term in terms:
            if len(term) < 1:
                continue
            sql = "SELECT term, standard_name, table_name, column_name, description FROM glossary WHERE term MATCH :term LIMIT 5"
            result = await meta_repo.session.execute(text(sql), {"term": term})
            for row in result.fetchall():
                matched.append({
                    "term": row[0], "standard_name": row[1],
                    "table_name": row[2], "column_name": row[3],
                    "description": row[4],
                })

        seen = set()
        unique = []
        for m in matched:
            key = m["term"]
            if key not in seen:
                seen.add(key)
                unique.append(m)

        logger.info(f"Glossary matching: {len(unique)} matches")
        return {"glossary_matches": unique[:20]}
    except Exception as e:
        logger.error(f"Glossary matching error: {e}")
        return {"glossary_matches": []}
