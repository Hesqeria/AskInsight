"""Decision insight node: based on query results + anomaly + attribution -> generate concise decision suggestions

Output format (DI-1/7/8 fix):
  Each suggestion must include:
  1. [Priority] P0/P1/P2
  2. [Finding] specific numbers (e.g. "East China sales 70,293, only 61% of South China")
  3. [Suggestion] actionable item + quantified target (e.g. "suggest increasing East China Q3 marketing budget by 20%")
  4. [Expected] expected effect (e.g. "expected to narrow the gap to within 15%")

At most 3 suggestions (DI-8 anti-bloat)
"""
import json
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger


DECISION_PROMPT = """You are an enterprise data analysis decision consultant. Based on the following data, generate no more than 3 concise decision suggestions.

[Query question]{query}

[Core data (TOP5)]
{data}

[Anomaly detection]
{anomaly}

[Attribution analysis]
{attribution}

[Dimension split (DWS layer)]
{drill_data}

[Strict format requirements]
1. Each suggestion must include specific numbers (citing the above data); vague statements are forbidden
2. Each suggestion format:
   [Priority] Finding: specific data facts
   -> Suggestion: actionable item (with quantified target)
   -> Expected: expected effect (quantifiable)
3. At most 3 suggestions, sorted by importance
4. If data is normal without anomalies, point out 1 growth opportunity
5. If data has anomalies, prioritize countermeasures
6. Plain text output, no markdown code blocks

Example output:
[P0] Finding: East China total sales 70,293, only 62% of South China (113,087)
-> Suggestion: increase East China Q3 marketing budget by 20%, focus on promoting digital category
-> Expected: East China sales expected to rise to 90,000, narrowing the gap with South China to 80%

[P1] Finding: food category sales share only 0.1%, far below digital (74%)
-> Suggestion: launch 3 new products in food category + bundling sales strategy
-> Expected: food share rises to 5%, adding about 500k incremental revenue

Output:"""


async def decision_insight(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Decision insight node: runs after drill_down_analysis"""
    writer = runtime.stream_writer
    writer({"stage": "Decision Insight"})
    try:
        query = state.get("query", "")
        result = state.get("_last_result", [])

        # Anomaly info
        anomaly_status = state.get("anomaly_status", "normal")
        anomaly_info = "No anomaly" if anomaly_status == "normal" else f"Anomaly status: {anomaly_status}, Z-Score: {state.get('anomaly_z_score', '?')}"

        # Attribution info
        drill = state.get("drill_down_result", {})
        attribution = drill.get("attribution", {})
        attr_str = "No attribution data" if not attribution else f"Top factor: {attribution.get('top_factor','')}, contribution: {attribution.get('contribution_pct',0):.1f}%"

        # DWS split data
        drill_data = ""
        for key in ("dws_region", "dws_category", "dwd_detail"):
            items = drill.get(key, [])
            if items:
                drill_data += f"\n{key}:\n"
                for item in items[:5]:
                    drill_data += f"  {item}\n"

        # Build prompt
        data_str = json.dumps(result[:5], ensure_ascii=False, default=str) if result else "No data"
        prompt = DECISION_PROMPT.replace("{query}", query[:100]) \
                                 .replace("{data}", data_str) \
                                 .replace("{anomaly}", anomaly_info) \
                                 .replace("{attribution}", attr_str) \
                                 .replace("{drill_data}", drill_data or "No drill-down data")

        # Call LLM
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        insights = resp.content.strip()

        # Clean up markdown
        if insights.startswith("```"):
            lines = insights.split("\n")
            insights = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        logger.info(f"Decision insights generated: {len(insights)} chars")
        writer({"insights": insights})

        return {"decision_insights": insights}
    except Exception as e:
        logger.error(f"Decision insight error: {e}")
        return {"decision_insights": ""}
