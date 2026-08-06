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
    drill_down_result: dict
    decision_insights: str  # drill-down attribution result  # dimension value exact match
    glossary_matches: list  # glossary matches
    feedback_examples: list  # historical corrected SQL  # multi-turn dialogue historical queries
