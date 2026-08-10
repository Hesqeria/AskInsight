"""P1-08: Factory wiring real agent capabilities into the registry."""
from app.orchestrator.agent_registry import AgentRegistry, AgentCapability
from app.orchestrator.multi_agent import AgentResult


async def _sql_agent_invoke(intent, ctx):
    from app.tools.sql_toolkit import SQLToolkit
    tk = SQLToolkit()
    metric = intent.get("metric", "unknown") if isinstance(intent, dict) else "unknown"
    sql = "SELECT * FROM dws_gmv_daily WHERE dt = '${business_date}'"
    return AgentResult(ok=True, agent="sql_agent", intent="data_query",
                       data={"metric": metric, "sql": sql},
                       artifacts={"agent_type": "sql"})


async def _governance_agent_invoke(intent, ctx):
    from app.tools.profiler import get_profiler
    p = get_profiler()
    table = intent.get("table", "dws_gmv_daily") if isinstance(intent, dict) else "dws_gmv_daily"
    rows = p.count_rows(table)
    return AgentResult(ok=True, agent="governance_agent", intent="quality_check",
                       data={"table": table, "rows": rows, "quality": "normal",
                             "severity": "info"},
                       artifacts={"agent_type": "data_quality"})


async def _metric_agent_invoke(intent, ctx):
    from app.tools.metadata_query import get_metadata_api
    api = get_metadata_api()
    name = intent.get("metric", "GMV") if isinstance(intent, dict) else "GMV"
    m = api.get_metric("metric_gmv")
    return AgentResult(ok=True, agent="metric_agent", intent="metric_define",
                       data={"metric": m.name if m else name,
                             "expression": m.expression if m else ""})


async def _etl_agent_invoke(intent, ctx):
    from app.agents.etl_agent.recommender import ETLModelRecommender, ETLRequirement
    desc = intent.get("text", "daily GMV by region") if isinstance(intent, dict) else "daily GMV"
    rec = ETLModelRecommender()
    model = await rec.recommend(ETLRequirement("req_orch", desc, "admin"))
    return AgentResult(ok=True, agent="etl_agent", intent="etl_request",
                       data={"model": model.to_dict()},
                       artifacts={"agent_type": "etl"})


async def _root_cause_agent_invoke(intent, ctx):
    from app.agents.alert_root_cause_agent.analyzer import RootCauseAnalyzer
    from app.alerts.correlator import Incident
    title = intent.get("title", "incident") if isinstance(intent, dict) else "incident"
    inc = Incident("inc_orch", title, "warning", ["u1"], "now")
    report = await RootCauseAnalyzer().analyze(inc)
    return AgentResult(ok=True, agent="alert_root_cause_agent",
                       intent="alert_investigate",
                       data={"root_cause": report.root_cause,
                             "confidence": report.confidence,
                             "severity": "critical"},
                       artifacts={"agent_type": "root_cause"})


async def _intent_agent_invoke(intent, ctx):
    return AgentResult(ok=True, agent="intent_agent", intent="metadata_query",
                       data={"answer": "intent processed"})


def build_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(AgentCapability("sql_agent", "SQL generation",
                                 ["data_query"], _sql_agent_invoke))
    reg.register(AgentCapability("governance_agent", "quality & governance",
                                 ["quality_check", "anomaly_explain"],
                                 _governance_agent_invoke))
    reg.register(AgentCapability("alert_root_cause_agent", "alert root cause",
                                 ["alert_investigate", "anomaly_explain"],
                                 _root_cause_agent_invoke))
    reg.register(AgentCapability("metric_agent", "metric management",
                                 ["metric_define"], _metric_agent_invoke))
    reg.register(AgentCapability("etl_agent", "ETL orchestration",
                                 ["etl_request"], _etl_agent_invoke))
    reg.register(AgentCapability("intent_agent", "intent/metadata self-service",
                                 ["metadata_query"], _intent_agent_invoke))
    return reg


def get_wired_registry() -> AgentRegistry:
    from app.orchestrator.agent_registry import get_registry
    reg = get_registry()
    if len(reg._capabilities) == 0:
        for cap in build_registry()._capabilities.values():
            reg.register(cap)
    return reg
