"""NL2SQL exemplar store (Vanna-style question-SQL pair flywheel).

Sources of verified pairs:
  - user_correction : feedback_log rows where the user supplied a fixed SQL
  - executed        : audit_log success rows that returned data
Pairs are retrieved by embedding similarity and injected as few-shot
context at SQL generation time.
"""
import hashlib
import math

from sqlalchemy import text

from app.core.log import logger

_SOURCE_CORRECTION = "user_correction"
_SOURCE_EXECUTED = "executed"
_MAX_AUDIT_SEED = 100


def _norm(question: str) -> str:
    return "".join((question or "").lower().split())


def exemplar_id(question: str) -> str:
    return hashlib.md5(_norm(question).encode("utf-8")).hexdigest()


def cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def format_for_prompt(exemplars) -> str:
    if not exemplars:
        return "(none)"
    blocks = []
    for e in exemplars:
        blocks.append(f"Q: {e['question']}\nSQL:\n{e['sql_text']}")
    return "\n\n".join(blocks)


async def add_exemplar(session, question: str, sql_text: str,
                       source: str, request_id: str = "") -> bool:
    """Idempotent insert keyed by md5(normalized question)."""
    q = (question or "").strip()
    sql_text = (sql_text or "").strip()
    if not q or not sql_text or "does not exist" in sql_text.lower():
        return False
    if len(sql_text) > 4000:
        return False
    try:
        # supersede any prior version of this question (open-ontologies
        # #109 lineage vocabulary): corrected/better SQL must win
        await session.execute(text(
            "DELETE FROM data_agent.nl2sql_exemplar "
            "WHERE exemplar_id = :eid"), {"eid": exemplar_id(q)})
        await session.execute(text(
            "INSERT INTO data_agent.nl2sql_exemplar "
            "(exemplar_id, question, sql_text, source, request_id, created_at) "
            "VALUES (:eid, :q, :s, :src, :rid, :ts)"),
            {"eid": exemplar_id(q), "q": q[:500], "s": sql_text,
             "src": source, "rid": request_id,
             "ts": __import__("datetime").datetime.now()})
        await session.commit()
        return True
    except Exception as e:
        logger.warning(f"add_exemplar failed: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return False


async def seed_from_feedback(session) -> int:
    """Pull verified pairs from feedback_log + audit_log. Idempotent."""
    added = 0
    try:
        rows = (await session.execute(text(
            "SELECT query, corrected_sql, username FROM data_agent.feedback_log "
            "WHERE corrected_sql IS NOT NULL AND corrected_sql != '' "
            "ORDER BY created_at DESC LIMIT 50"))).fetchall()
        for q, corrected, user in rows:
            if await add_exemplar(session, q, corrected,
                                  _SOURCE_CORRECTION, user or ""):
                added += 1
        rows = (await session.execute(text(
            "SELECT query, sql_text, request_id FROM data_agent.audit_log "
            "WHERE status = 'success' AND result_rows > 0 AND sql_text != '' "
            "AND sql_text LIKE 'SELECT%' "
            "ORDER BY created_at DESC LIMIT " + str(_MAX_AUDIT_SEED)))).fetchall()
        for q, sql_text, rid in rows:
            if await add_exemplar(session, q, sql_text, _SOURCE_EXECUTED, rid or ""):
                added += 1
        if added:
            logger.info(f"exemplar store seeded: +{added} pairs")
    except Exception as e:
        logger.warning(f"seed_from_feedback failed: {e}")
    return added


async def search(session, embedding_client, question: str, top_k: int = 2) -> list:
    """Dual-path recall for few-shot exemplars (RAG 双路召回).

    Lane A (dense): question-embedding cosine similarity.
    Lane B (sparse): character-bigram Jaccard - language-agnostic lexical
    match catching shared phrasing/digits even when embeddings drift.
    Lanes are RRF-fused; a dense-similarity floor keeps unrelated
    examples away from the LLM lane.
    """
    from app.core.embed_cache import embed_cached
    from app.core.recall_fusion import rrf_fuse, char_ngram_similarity
    try:
        rows = (await session.execute(text(
            "SELECT question, sql_text FROM data_agent.nl2sql_exemplar "
            "LIMIT 200"))).fetchall()
        if not rows:
            return []
        qvec = await embed_cached(embedding_client, question)
        if qvec is None:
            qvec = await embedding_client.aembed_query(question)
        sims = {}
        for q, sql_text in rows:
            evec = await embed_cached(embedding_client, q)
            if evec is None:
                evec = await embedding_client.aembed_query(q)
            sims[q] = cosine(qvec, evec)
        dense = [q for q, _ in sorted(sims.items(), key=lambda kv: -kv[1])]
        sparse = sorted((q for q, _ in rows),
                        key=lambda q: -char_ngram_similarity(question, q))
        fused = rrf_fuse([dense, sparse], k=60, weights=[1.0, 0.8])
        by_q = {q: sql_text for q, sql_text in rows}
        out = []
        for q in fused:
            if sims.get(q, 0.0) <= 0.5:
                continue
            out.append({"question": q, "sql_text": by_q[q],
                        "score": round(sims.get(q, 0.0), 4),
                        "sparse": round(char_ngram_similarity(question, q), 4)})
            if len(out) >= top_k:
                break
        return out
    except Exception as e:
        logger.warning(f"exemplar search failed: {e}")
        return []
