"""P3 Agent Engine integration tests."""
import sys; sys.path.insert(0, r'D:\大模型\智能问数\AskInsightackend')


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
