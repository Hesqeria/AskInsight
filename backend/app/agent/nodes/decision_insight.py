"""Decision insight node: generates decision suggestions from query results + anomaly data."""
import json
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger
from langchain_core.messages import HumanMessage

DECISION_PROMPT = """[Query question]{query}
[Analysis data]{data}
[Anomaly information]{anomaly}
[Attribution analysis]{attribution}
[Drill-down data]{drill_data}

Please generate decision suggestions based on the above data.
"""


async def decision_insight(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "Decision Insight"})
    try:
        query = state.get("query", "")
        result = state.get("_last_result", [])
        anomaly = state.get("anomaly_result", {})
        attribution = state.get("attribution_result", {})
        drill = state.get("drill_down_result", {})

        data_str = json.dumps(result[:5], ensure_ascii=False, default=str) if result else "No data"
        anomaly_info = json.dumps(anomaly, ensure_ascii=False, default=str) if anomaly else "No anomaly detected"
        attr_str = json.dumps(attribution, ensure_ascii=False, default=str) if attribution else "No attribution data"
        drill_data = json.dumps(drill, ensure_ascii=False, default=str) if drill else "No drill-down data"

        # Use str.format() instead of sequential .replace() to prevent injection
        prompt = DECISION_PROMPT.format(
            query=query[:100],
            data=data_str[:2000],
            anomaly=anomaly_info[:500],
            attribution=attr_str[:500],
            drill_data=drill_data[:500],
        )

        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        insight = str(resp.content).strip()

        logger.info(f"Decision insight generated ({len(insight)} chars)")
        return {"decision_insight": insight}
    except Exception as e:
        logger.error(f"Decision insight error: {e}")
        return {"decision_insight": ""}
