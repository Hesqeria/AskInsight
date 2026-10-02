"""Glossary matching node: business terms -> standard field mapping."""
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.keywords import sanitize_keywords
from app.core.log import logger
from sqlalchemy import text

# Shared column list (feedback_router reuses it for the admin listing).
GLOSSARY_COLS = "term, standard_name, table_name, column_name, description"


async def glossary_matching(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Glossary Matching"})
    try:
        meta_repo = runtime.context["meta_doris_repository"]
        query = state.get("query", "")
        keywords = state.get("keywords", [])

        terms = sanitize_keywords(keywords + [query])
        matched = []
        seen_terms = set()

        async def _fetch(term: str):
            if len(term) < 1 or term in seen_terms:
                return
            seen_terms.add(term)
            sql = f"SELECT {GLOSSARY_COLS} FROM data_agent.glossary WHERE term MATCH :term LIMIT 5"
            try:
                result = await meta_repo.session.execute(text(sql), {"term": term})
                rows = result.fetchall()
                for row in rows:
                    matched.append({
                        "term": row[0], "standard_name": row[1],
                        "table_name": row[2], "column_name": row[3],
                        "description": row[4],
                    })
            except Exception:
                pass

        # 1. 关键词精确匹配
        for term in terms:
            await _fetch(term)

        # 2. 整句包含匹配（解决 jieba 拆词导致"已支付"被拆成"已/支付"而漏匹配）
        if query:
            all_terms_sql = "SELECT DISTINCT term FROM data_agent.glossary WHERE status='active'"
            try:
                all_terms_res = await meta_repo.session.execute(text(all_terms_sql))
                all_terms_rows = all_terms_res.fetchall()
            except Exception:
                all_terms_rows = []
            for row in all_terms_rows:
                t = row[0]
                if t and t in query and t not in seen_terms:
                    await _fetch(t)

        seen = set()
        unique = []
        for m in matched:
            key = (m["term"], m["table_name"], m["column_name"])
            if key not in seen:
                seen.add(key)
                unique.append(m)

        logger.info(f"Glossary matching: {len(unique)} matches")
        return {"glossary_matches": unique[:20]}
    except Exception as e:
        logger.error(f"Glossary matching error: {e}")
        return {"glossary_matches": []}
