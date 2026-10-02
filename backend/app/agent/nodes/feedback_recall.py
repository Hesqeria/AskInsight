"""Get-better-with-each-question node: recall historical examples of corrected SQL"""
import re
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
from sqlalchemy import text

MAX_FEWSHOT = 3  # 最多注入 3 个 Few-Shot 示例

# 危险 SQL 过滤（防止 Few-Shot 或反馈里的恶意 SQL 被注入 prompt）
_DANGEROUS_RE = re.compile(
    r"\b(drop|delete|truncate|alter|insert|update|grant|revoke|execute)\b",
    re.IGNORECASE,
)


async def _recall_few_shot(meta_repo, query: str) -> list[dict]:
    """从 few_shot_examples 表召回相似问题示例。

    用关键词匹配（简化版，后续可换 Milvus 向量召回）。
    """
    examples = []
    # 提取 query 中的关键词（2~4 字中文词 + 英文术语）
    keywords = []
    for kw in ["GMV", "订单", "用户", "商品", "SKU", "复购", "留存", "客单价",
               "趋势", "分布", "排名", "占比", "环比", "平均", "支付", "优惠券",
               "活动", "退款", "评价", "会员", "地域", "省份", "昨天", "今天"]:
        if kw in query:
            keywords.append(kw)

    if not keywords:
        return examples

    # 用 LIKE 匹配任一关键词（OR 组合）
    try:
        conds = " OR ".join(["question LIKE :k%d" % i for i in range(len(keywords))])
        params = {"k%d" % i: f"%{k}%" for i, k in enumerate(keywords)}
        sql = f"""
            SELECT example_id, question, sql_text, entity, complexity
            FROM data_agent.few_shot_examples
            WHERE ({conds}) AND source = 'GOLD'
            ORDER BY created_at DESC
            LIMIT {MAX_FEWSHOT * 2}
        """
        result = await meta_repo.session.execute(text(sql), params)
        seen_questions = set()
        for row in result.fetchall():
            example_id = row[0]
            q = row[1]
            sql_text = row[2] or ''
            if q in seen_questions:
                continue
            if _DANGEROUS_RE.search(sql_text):
                continue
            seen_questions.add(q)
            examples.append({
                "query": q, "sql": sql_text,
                "entity": row[3], "complexity": row[4],
                "example_id": example_id,
            })
            # Hit tracking for monthly pruning (non-fatal).
            try:
                from app.services.feedback_flowback import bump_hit
                await bump_hit(example_id, meta_repo.session)
            except Exception:
                pass
            if len(examples) >= MAX_FEWSHOT:
                break
    except Exception as e:
        logger.warning(f"few_shot recall error: {e}")

    return examples


async def _recall_feedback(meta_repo, query: str) -> list[dict]:
    """从 feedback_log 召回人工纠正的 SQL（兜底）。"""
    examples = []
    try:
        sql = """
            SELECT query, corrected_sql FROM data_agent.feedback_log
            WHERE query LIKE :pattern
            ORDER BY created_at DESC LIMIT 3
        """
        result = await meta_repo.session.execute(text(sql), {"pattern": f"%{query[:10]}%"})
        for row in result.fetchall():
            sql_text = row[1] or ''
            if _DANGEROUS_RE.search(sql_text):
                continue
            examples.append({"query": row[0], "sql": sql_text})
    except Exception as e:
        logger.warning(f"feedback recall error: {e}")
    return examples


async def feedback_recall(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Historical Experience Recall"})
    try:
        meta_repo = runtime.context["meta_doris_repository"]
        query = state.get("query", "")

        # 优先从 few_shot_examples 召回（语义相似的金标准 SQL）
        few_shot = await _recall_few_shot(meta_repo, query)

        # 兜底：feedback_log 人工纠正
        feedback = await _recall_feedback(meta_repo, query)

        examples = few_shot + feedback
        logger.info(
            f"Historical experience recall: {len(examples)} items "
            f"(few_shot={len(few_shot)}, feedback={len(feedback)})"
        )
        return {"feedback_examples": examples}
    except Exception as e:
        logger.error(f"Historical experience recall error: {e}")
        return {"feedback_examples": []}
