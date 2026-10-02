"""Prometheus metric definitions (P2-D1: thread-safe + P2-D2: label-controlled)."""
from prometheus_client import Counter, Histogram, Gauge

# P2-D2: Use only fixed dimensions as labels (do not use query/SQL as labels)
QUERY_TOTAL = Counter(
    "data_agent_query_total",
    "Total queries",
    ["status"]  # success / error / cache_hit
)

QUERY_LATENCY = Histogram(
    "data_agent_query_latency_seconds",
    "Query latency (seconds)",
    ["status"],
    buckets=[1, 5, 10, 30, 60, 120, 300]
)

SQL_GENERATED = Counter(
    "data_agent_sql_generated_total",
    "Total SQL generated"
)

SQL_CORRECTED = Counter(
    "data_agent_sql_corrected_total",
    "SQL correction count"
)

CACHE_HITS = Counter(
    "data_agent_cache_hits_total",
    "Cache hit count"
)

ACTIVE_CONNECTIONS = Gauge(
    "data_agent_active_connections",
    "Active connection count"
)

# R3: SQL pass-rate metric
SQL_PASS_RATE = Gauge(
    'data_agent_sql_pass_rate',
    'SQL first-validation pass rate'
)

# Rerank (gte-rerank-v2) observability
RERANK_CALLS = Counter(
    "data_agent_rerank_calls_total",
    "Rerank invocations",
    ["status"],  # success / cache_hit / error
)

RERANK_LATENCY = Histogram(
    "data_agent_rerank_latency_seconds",
    "Rerank call latency (seconds, includes cache hits as ~0)",
    ["status"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
)

RERANK_CANDIDATES = Histogram(
    "data_agent_rerank_candidates",
    "Number of candidates submitted to the reranker per call",
    buckets=[1, 5, 10, 20, 30, 50, 100],
)

# RL (Thompson Sampling bandit on decision_insight)
RL_POLICY_CHOSEN = Counter(
    "data_agent_rl_policy_chosen_total",
    "How many times each decision policy was selected",
    ["policy"],
)

RL_REWARD = Counter(
    "data_agent_rl_reward_total",
    "RL rewards attributed to policies",
    ["policy", "reward"],  # reward = "positive" or "negative"
)

# PII gate (correct_sql scans corrected SQL against the ontology)
PII_GATE_DECISIONS = Counter(
    "data_agent_pii_gate_decisions_total",
    "PII gate outcomes in correct_sql",
    ["outcome"],  # "blocked" / "passed" / "skipped"
)

# Clarification workflow (模糊提问澄清交互-PRD.md P3-CLARIFY-16 A/B 埋点)
CLARIFY_TRIGGERED = Counter(
    "data_agent_clarify_triggered_total",
    "Clarification sessions created by ask_clarification",
    ["reason"],  # "low_confidence" / "missing_field"
)

CLARIFY_OUTCOME = Counter(
    "data_agent_clarify_outcome_total",
    "Clarification session outcomes",
    ["outcome"],  # confirmed / amended / abandoned / forced
)

CLARIFY_ROUNDS = Histogram(
    "data_agent_clarify_rounds",
    "Rounds taken to resolve a clarification session",
    buckets=[1, 2, 3, 4],
)

CLARIFY_CONFIDENCE_DELTA = Histogram(
    "data_agent_clarify_confidence_delta",
    "Confidence improvement after clarification merge",
    buckets=[0, 0.2, 0.4, 0.6, 0.8, 1.0],
)


# --------------------------------------------------------------------- #
# M8: token meter / context pressure (PRD 05-DeepSeek-Harness研究)
# --------------------------------------------------------------------- #
CONTEXT_TOKENS_ESTIMATED = Histogram(
    "data_agent_context_tokens_estimated",
    "Estimated prompt tokens injected into generate_sql (pre-budget)",
    buckets=[2000, 4000, 8000, 16000, 32000, 64000, 128000],
)

CONTEXT_TRIMMED_PARTS = Counter(
    "data_agent_context_trimmed_parts_total",
    "Prompt parts trimmed by the injection budget",
)
