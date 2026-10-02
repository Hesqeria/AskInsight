from typing import TypedDict


class ColumnInfoState(TypedDict):
    name: str
    type: str
    role: str
    examples: list
    description: str
    alias: list[str]


class TableInfoState(TypedDict):
    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class MetricInfoState(TypedDict):
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]


class DateInfoState(TypedDict):
    date: str
    weekday: str
    quarter: str
    current_date_id: int
    current_month: int
    current_year: int


class DBInfoState(TypedDict):
    version: str
    dialect: str


class DataAgentState(TypedDict, total=False):
    query: str
    keywords: list[str]
    retrieved_columns: list
    retrieved_metrics: list
    retrieved_values: list
    table_infos: list[TableInfoState]
    metric_infos: list[MetricInfoState]
    date_info: DateInfoState
    db_info: DBInfoState
    sql: str
    error: str | None
    intent: str   # query / chat
    chat_reply: str  # chitchat reply
    history: list[str]
    matched_dimension_values: list
    _last_result: list  # result of execute_sql (used by downstream nodes)
    anomaly_status: str  # normal/warning/critical
    sql_source: str  # "plan_render" | "llm" — renderer output skips correct_sql degradation
    drill_down_result: dict
    decision_insights: str  # drill-down attribution result  # dimension value exact match
    glossary_matches: list  # glossary matches
    feedback_examples: list  # historical corrected SQL  # multi-turn dialogue historical queries
    # RL (Thompson Sampling bandit) bookkeeping for decision_insight.
    # `decision_policy` records which policy generated `decision_insights`
    # so the reward endpoint can attribute the user rating correctly.
    decision_policy: str
    # PII gate signal set by correct_sql after scanning the corrected
    # SQL against the ontology (PRD §6.2). Downstream nodes / the UI can
    # branch on this to require human approval before execution.
    needs_approval: bool
    approval_reason: str
    # Approval workflow bookkeeping. When wait_approval fires it stores
    # the ticket_id here and the conditional edge routes to END; the
    # resumed graph (after /api/approvals decision) clears this.
    pending_approval_ticket_id: str
    _username: str  # passed in by query_service for ticket attribution
    _request_id: str  # set by query_service; used by lineage/candidate persistence
    # Semantic plan from semantic_grounding node (NL→IR before SQL gen).
    # Dict shape matches app.ontology.plan.SemanticPlan.to_dict(). When
    # non-null and confidence >= 0.7, generate_sql can render SQL from
    # the plan deterministically instead of calling the LLM.
    semantic_plan: dict
    # Clarification workflow bookkeeping (PRD: 模糊提问澄清交互-PRD.md).
    # Set by ask_clarification when confidence < 0.7. The conditional
    # edge routes to END; the resumed graph (after /api/v1/clarify/{id}/resume)
    # runs merge_clarification then continues to generate_sql.
    pending_clarify_id: str
    # Carries the user's response from the resume endpoint into the
    # merge_clarification node (selections / free_text / confirmed).
    clarify_user_response: dict
    # Carries the loaded ClarifySession snapshot for merge_clarification
    # (so the node doesn't need to re-fetch from DB).
    clarify_session: dict
    # Flag indicating clarify was skipped due to missing DB table.
    clarify_skipped: bool
    # Internal (underscore) keys MUST be declared here - LangGraph drops
    # unknown keys from node patches, which silently broke the PII
    # sticky-approval flag (infinite execute_sql<->wait_approval loop)
    # and the M10 spill locator downstream.
    _last_result: list            # execute_sql result (post head/tail view)
    _result_truncated: bool
    _total_rows: int
    _pii_masked_cells: int
    _spill_id: str                # M10: full-result locator ('' when none)
    _pii_approved: bool           # M7: sticky approval for this turn
