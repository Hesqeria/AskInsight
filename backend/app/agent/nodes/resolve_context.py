"""Multi-turn dialogue: coreference resolution based on historical queries"""
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger

RESOLVE_PROMPT = """You are a query rewriting assistant. Based on the conversation history, rewrite the user's latest question into a standalone, complete question.

Rules:
1. If the latest question contains pronouns (e.g. "there" "it" "among them") or omissions (e.g. "then what about East China"), complete it with historical context
2. If the latest question is already complete, return it as-is
3. Output only the rewritten question, with no explanation

Conversation history:
{history}

Latest question:{query}

Rewritten complete question:"""


async def resolve_context(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """If there is multi-turn history, perform coreference resolution"""
    history = state.get("history", [])
    query = state.get("query", "")
    if not history:
        return {"query": query}
    try:
        history_text = "\n".join([f"User: {h}" for h in history[-3:]])  # last 3 turns
        prompt = RESOLVE_PROMPT.replace("{history}", history_text).replace("{query}", query)
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        rewritten = resp.content.strip().strip('"').strip("'")
        logger.info(f"Coreference resolution: '{query}' -> '{rewritten}'")
        return {"query": rewritten}
    except Exception as e:
        logger.warning(f"Coreference resolution failed, using original query: {e}")
        return {"query": query}
