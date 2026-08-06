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
