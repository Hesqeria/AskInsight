from langgraph.constants import START, END
from langgraph.graph import StateGraph

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.nodes.intent_recognition import intent_recognition
from app.agent.nodes.resolve_context import resolve_context
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.extract_lineage import extract_lineage
from app.agent.nodes.anomaly_detection import anomaly_detection
from app.agent.nodes.drill_down_analysis import drill_down_analysis
from app.agent.nodes.decision_insight import decision_insight
from app.agent.nodes.code_executor import code_executor
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.glossary_matching import glossary_matching
from app.agent.nodes.feedback_recall import feedback_recall
from app.agent.nodes.match_dimension_value import match_dimension_value
from app.agent.nodes.semantic_grounding import semantic_grounding
from app.agent.nodes.ask_clarification import ask_clarification, needs_clarification
from app.agent.nodes.merge_clarification import merge_clarification
from app.agent.nodes.validate_sql_safety import validate_sql_safety_node
from app.agent.nodes.validate_sql import validate_sql
from app.agent.nodes.assess_complexity import assess_complexity
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.wait_approval import wait_approval


graph_builder = StateGraph(
    state_schema=DataAgentState,
    context_schema=DataAgentContext,
)

graph_builder.add_node("intent_recognition", intent_recognition)
graph_builder.add_node("resolve_context", resolve_context)
graph_builder.add_node("extract_keywords", extract_keywords)
graph_builder.add_node("recall_column", recall_column)
graph_builder.add_node("recall_metric", recall_metric)
graph_builder.add_node("recall_value", recall_value)
graph_builder.add_node("merge_retrieved_info", merge_retrieved_info)
graph_builder.add_node("filter_table", filter_table)
graph_builder.add_node("filter_metric", filter_metric)
graph_builder.add_node("add_extra_context", add_extra_context)
graph_builder.add_node("generate_sql", generate_sql)
graph_builder.add_node("glossary_matching", glossary_matching)
graph_builder.add_node("feedback_recall", feedback_recall)
graph_builder.add_node("match_dimension_value", match_dimension_value)
graph_builder.add_node("semantic_grounding", semantic_grounding)
graph_builder.add_node("ask_clarification", ask_clarification)
graph_builder.add_node("merge_clarification", merge_clarification)
graph_builder.add_node("validate_sql_safety", validate_sql_safety_node)
graph_builder.add_node("validate_sql", validate_sql)
graph_builder.add_node("assess_complexity", assess_complexity)
graph_builder.add_node("correct_sql", correct_sql)
graph_builder.add_node("wait_approval", wait_approval)
graph_builder.add_node("execute_sql", execute_sql)
graph_builder.add_node("extract_lineage", extract_lineage)
graph_builder.add_node("anomaly_detection", anomaly_detection)
graph_builder.add_node("drill_down_analysis", drill_down_analysis)
graph_builder.add_node("decision_insight", decision_insight)
graph_builder.add_node("code_executor", code_executor)

# Entry: intent recognition first
graph_builder.add_edge(START, "intent_recognition")

# Conditional branch: chitchat -> END, query -> extract_keywords
graph_builder.add_conditional_edges(
    "intent_recognition",
    lambda state: "resolve_context" if state.get("intent") == "query" else END,
    {"resolve_context": "resolve_context", END: END},
)

# Query main flow
graph_builder.add_edge("resolve_context", "extract_keywords")
graph_builder.add_edge("extract_keywords", "recall_column")
graph_builder.add_edge("extract_keywords", "recall_metric")
graph_builder.add_edge("extract_keywords", "recall_value")
graph_builder.add_edge("recall_column", "merge_retrieved_info")
graph_builder.add_edge("recall_metric", "merge_retrieved_info")
graph_builder.add_edge("recall_value", "merge_retrieved_info")
graph_builder.add_edge("merge_retrieved_info", "filter_table")
graph_builder.add_edge("merge_retrieved_info", "filter_metric")
graph_builder.add_edge("filter_table", "add_extra_context")
graph_builder.add_edge("filter_metric", "add_extra_context")
graph_builder.add_edge("add_extra_context", "glossary_matching")
graph_builder.add_edge("glossary_matching", "feedback_recall")
graph_builder.add_edge("feedback_recall", "match_dimension_value")
graph_builder.add_edge("match_dimension_value", "semantic_grounding")
# Clarification gate: confidence < 0.7 → ask_clarification (terminates
# this request via its own conditional edge; resumed graph skips).
# Otherwise → generate_sql directly.
graph_builder.add_conditional_edges(
    "semantic_grounding",
    lambda state: "ask_clarification" if needs_clarification(state) else "generate_sql",
    {"ask_clarification": "ask_clarification", "generate_sql": "generate_sql"},
)
# ask_clarification sets pending_clarify_id; conditional edge routes to
# END (terminate this SSE request, awaiting user response). When None
# (degraded mode), fall through to generate_sql.
graph_builder.add_conditional_edges(
    "ask_clarification",
    lambda state: END if state.get("pending_clarify_id") else "generate_sql",
    {END: END, "generate_sql": "generate_sql"},
)
# merge_clarification is the entry point of the RESUMED graph (set by
# clarify_router); it always falls through to generate_sql.
graph_builder.add_edge("merge_clarification", "generate_sql")
graph_builder.add_edge("generate_sql", "validate_sql_safety")
graph_builder.add_edge("validate_sql_safety", "validate_sql")
graph_builder.add_conditional_edges(
    "validate_sql",
    lambda state: "assess_complexity" if state.get("error") is None else "correct_sql",
    {"assess_complexity": "assess_complexity", "correct_sql": "correct_sql"},
)

# Complexity-aware routing: simple/medium execute directly, complex goes through correct_sql degradation
graph_builder.add_conditional_edges(
    "assess_complexity",
    lambda state: "execute_sql" if state.get("sql_source") == "plan_render" or state.get("complexity") in ("simple", "medium", "fallback") else "correct_sql",
    {"execute_sql": "execute_sql", "correct_sql": "correct_sql"},
)

# PII gate: after correct_sql runs (with its post-correction PII scan),
# route to wait_approval when the gate triggers, else execute_sql as before.
# `wait_approval` itself terminates the request via its own conditional
# edge (pending_approval -> END; auto-approve -> execute_sql).
graph_builder.add_conditional_edges(
    "correct_sql",
    lambda state: "wait_approval" if state.get("needs_approval") else "execute_sql",
    {"wait_approval": "wait_approval", "execute_sql": "execute_sql"},
)
graph_builder.add_conditional_edges(
    "wait_approval",
    lambda state: END if state.get("pending_approval_ticket_id") else "execute_sql",
    {END: END, "execute_sql": "execute_sql"},
)
# M6/M7: PII guard may fire on the direct (simple) path too - route
# to wait_approval instead of executing; approval resume re-enters
# execute_sql with _pii_approved set (no loop).
graph_builder.add_conditional_edges(
    "execute_sql",
    lambda state: "wait_approval" if state.get("needs_approval") else "extract_lineage",
    {"wait_approval": "wait_approval", "extract_lineage": "extract_lineage"},
)
graph_builder.add_edge("extract_lineage", "anomaly_detection")
graph_builder.add_edge("anomaly_detection", "drill_down_analysis")
graph_builder.add_edge("drill_down_analysis", "decision_insight")
graph_builder.add_edge("decision_insight", "code_executor")
graph_builder.add_edge("code_executor", END)

graph = graph_builder.compile()
