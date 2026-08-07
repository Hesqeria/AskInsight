"""Multi-turn dialogue: coreference resolution with history sanitization."""
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger

MAX_HISTORY_ITEMS = 3
MAX_HISTORY_LENGTH = 200

RESOLVE_PROMPT = """You are a query rewriting assistant. Based on the conversation history, rewrite the user's latest question into a standalone, complete question.

Rules:
1. If the latest question contains pronouns or omissions, complete it with historical context
2. If the latest question is already complete, return it as-is
3. Output only the rewritten question, with no explanation

Conversation history:
{history}

Latest question:{query}

Rewritten complete question:"""


def _clean_history(history: list) -> str:
    """Sanitize history items: truncate, strip control chars."""
    cleaned = []
    for i, h in enumerate(history[-MAX_HISTORY_ITEMS:]):
        s = str(h)[:MAX_HISTORY_LENGTH]
        # Strip control characters / prompt injection delimiters
        s = s.replace("{", "(").replace("}", ")")
        s = s.replace("\n", " ").replace("\r", "")
        cleaned.append(f"User: {s}")
    return "\n".join(cleaned)


async def resolve_context(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    history = state.get("history", [])
    query = state.get("query", "")
    if not history:
        return {"query": query}
    try:
        history_text = _clean_history(history)
        prompt = RESOLVE_PROMPT.format(history=history_text, query=query[:MAX_HISTORY_LENGTH])
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        rewritten = str(resp.content).strip().strip('"').strip("'")[:MAX_HISTORY_LENGTH]
        logger.info(f"Coreference: '{query[:50]}' -> '{rewritten[:50]}'")
        return {"query": rewritten}
    except Exception as e:
        logger.warning(f"Coreference resolution failed, using original query: {e}")
        return {"query": query}
