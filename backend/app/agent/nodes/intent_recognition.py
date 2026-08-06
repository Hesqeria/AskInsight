"""Intent recognition node: distinguish chitchat vs data query"""
import json
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger

INTENT_PROMPT = """Determine whether the user input is "data query" or "chitchat".

Data query: questions that require querying the database, statistics, calculation, or analysis (e.g. "calculate total sales" "which customer spent the most")
Chitchat: greetings, pleasantries, questions about AI itself, or conversations unrelated to data (e.g. "hello" "who are you" "thanks")

Output only one JSON, in the format: {{ "intent": "query" or "chat", "reply": "if chitchat, give a friendly reply; if query, leave empty" }}

User input:{query}"""


async def intent_recognition(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Intent Recognition"})
    try:
        query = state.get("query", "")
        prompt_msg = INTENT_PROMPT.replace("{query}", query)
        # Use LLM for synchronous judgment (avoid PromptTemplate to prevent variable conflicts)
        from langchain_core.messages import HumanMessage
        resp = await llm.ainvoke([HumanMessage(content=prompt_msg)])
        content = resp.content.strip()
        # Extract JSON (compatible with markdown wrapping)
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
        intent = data.get("intent", "query")
        reply = data.get("reply", "")
        logger.info(f"Intent recognition: {intent} | reply={reply[:30]}")
        # Chitchat: output the reply directly via writer (frontend displays after receiving result)
        if intent == "chat" and reply:
            writer({"result": [{"reply": reply}]})
        return {"intent": intent, "chat_reply": reply}
    except Exception as e:
        logger.warning(f"Intent recognition failed, treating as query by default: {e}")
        return {"intent": "query", "chat_reply": ""}
