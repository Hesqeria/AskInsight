"""P3 Agent Engine integration tests."""


def test_llm_router_import_and_priority():
    from app.infra.llm_router import TASK_MODEL_PRIORITY, get_llm
    assert "intent" in TASK_MODEL_PRIORITY
    assert "sql_complex" in TASK_MODEL_PRIORITY
    router = get_llm()
    assert router._select("intent", None)[0] == "deepseek-v4-flash"
    assert router._select("sql_complex", None)[0] == "deepseek-v4-pro"
    assert router._select("intent", "custom-model")[0] == "custom-model"


def test_llm_router_unavailable():
    from app.infra.llm_router import get_llm
    import time
    router = get_llm()
    router._mark_unavailable("test-model")
    assert router._is_unavailable("test-model") is True
    assert router._select("intent", "test-model") == [
        "test-model", "deepseek-v4-flash", "deepseek-v4-pro"
    ]


def test_llm_router_only_circuit_breaks_transient_errors():
    """4xx errors must NOT disable the primary model; only 5xx / 429 /
    transport errors should. Previously any failure (bad key, context
    overflow) disabled the model for 600s."""
    from app.infra.llm_router import LLMRouter
    import httpx

    class FakeResponse:
        def __init__(self, status):
            self.status_code = status

    router = LLMRouter()

    # 4xx (e.g. 401 bad key, 400 bad request) -> caller-side, do not break.
    err4xx = httpx.HTTPStatusError("bad", request=None,
                                   response=FakeResponse(401))
    assert router._is_transient(err4xx) is False

    # 5xx (e.g. 503 service unavailable) -> transient.
    err5xx = httpx.HTTPStatusError("oops", request=None,
                                   response=FakeResponse(503))
    assert router._is_transient(err5xx) is True

    # 429 (rate limited) -> transient.
    err429 = httpx.HTTPStatusError("slow down", request=None,
                                   response=FakeResponse(429))
    assert router._is_transient(err429) is True

    # Transport errors (no response attached) -> transient.
    assert router._is_transient(httpx.ConnectError("dns fail")) is True
    assert router._is_transient(httpx.ReadTimeout("slow")) is True


def test_prompt_manager():
    from app.infra.prompt_manager import PromptManager
    pm = PromptManager()
    result = pm.render("intent_agent", "main", {"query": "昨天 GMV", "metrics": "", "glossary": ""})
    assert "GMV" in result


def test_tool_registry():
    from app.infra.tool_registry import get_tool_registry
    reg = get_tool_registry()
    tools = reg.list_tools("L3_engineer")
    assert len(tools) >= 3
    assert any(t["name"] == "execute_sql" for t in tools)
    assert any(t["name"] == "query_metadata" for t in tools)


def test_tool_registry_invoke_enforces_role():
    """`invoke` must enforce required_role - previously the role check
    was only applied in list_tools, allowing any caller to execute SQL."""
    from app.infra.tool_registry import get_tool_registry
    reg = get_tool_registry()
    # L1 calling execute_sql must be denied without invoking the handler.
    denied = reg.invoke("execute_sql", {"sql": "SELECT 1"}, role="L1_business")
    assert denied["ok"] is False
    assert "Permission denied" in denied["error"]
    # L3 is allowed; the handler runs (and may fail on missing DB creds,
    # but the failure must come from the handler, not the RBAC gate).
    allowed = reg.invoke("run_anomaly_check", {}, role="L3_engineer")
    assert allowed["ok"] is True
    # Default role (when caller forgets to pass one) is the lowest, so
    # high-risk tools are still gated.
    denied_default = reg.invoke("execute_sql", {"sql": "SELECT 1"})
    assert denied_default["ok"] is False
    assert "Permission denied" in denied_default["error"]


def test_memory_manager():
    from app.infra.memory import get_memory
    mem = get_memory()
    mem.set("sid1", "key1", "value1")
    assert mem.get("sid1", "key1") == "value1"
    mem.add_message("sid2", "user", "hello")
    mem.add_message("sid2", "assistant", "hi there")
    hist = mem.get_history("sid2", 5)
    assert len(hist) == 2


def test_planner():
    from app.infra.planner import get_planner
    planner = get_planner()
    plan = planner.plan("data_query", {"metric": "GMV", "dimensions": ["region"]})
    assert plan.goal == "data_query: GMV"
    assert len(plan.steps) == 4
    step = plan.next_step()
    assert step["agent"] == "intent_agent"
    plan.complete_current({"intent": "data_query"})
    assert plan.steps[0]["status"] == "completed"
