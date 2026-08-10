# Data Warehouse Implementation Case — AskInsight

> Enterprise NL2SQL Agent on a multi-layer warehouse (ODS → DWD → DWS → ADS)
> This guide walks you through a complete, working implementation so you can start fast.

---

## 1. Business Scenario

A retail e-commerce company runs an analytic warehouse. Business users ask questions in
plain Chinese/English and get SQL-generated answers, charts, and governance insights —
without writing SQL.

- **Warehouse layers**: ODS (raw) → DWD (detail) → DWS (summary) → ADS (application)
- **Dimensions**: region / customer / product / date
- **Key metrics**: GMV, AOV, order count, customer count
- **Target**: NL2SQL with ≥93% warehouse pass rate, full governance loop

---

## 2. Warehouse Architecture

```
+-------------------------------------------------------------+
|  ADS   ads_customer_profile          (customer tags / RFM)   |
|        dws_region_summary            (region KPIs)           |
+-------------------------------------------------------------+
|  DWS   dws_sales_wide                (wide fact, multi-dim)  |
+-------------------------------------------------------------+
|  DWD   fact_order                    (order fact, 3 FKs)     |
+-------------------------------------------------------------+
|  ODS   (raw source tables imported from business DB)         |
+-------------------------------------------------------------+
|  DIM   dim_region / dim_customer / dim_product / dim_date    |
+-------------------------------------------------------------+
```

### 2.1 Reference DDL (Apache Doris)

```sql
-- Dimension: region
CREATE TABLE IF NOT EXISTS dim_region (
    region_id   BIGINT      COMMENT 'Region ID',
    province    VARCHAR(64) COMMENT 'Province',
    region_name VARCHAR(64) COMMENT 'Macro region',
    country     VARCHAR(32) COMMENT 'Country'
)
DUPLICATE KEY(region_id)
DISTRIBUTED BY HASH(region_id) BUCKETS 3
PROPERTIES ("replication_num" = "1");

-- Dimension: date
CREATE TABLE IF NOT EXISTS dim_date (
    date_id INT COMMENT 'yyyyMMdd',
    year    INT COMMENT 'Year',
    quarter INT COMMENT 'Quarter',
    month   INT COMMENT 'Month',
    day     INT COMMENT 'Day'
)
UNIQUE KEY(date_id)
DISTRIBUTED BY HASH(date_id) BUCKETS 3
PROPERTIES ("replication_num" = "1");

-- Fact: order (snowflake-ish, 3 foreign keys)
CREATE TABLE IF NOT EXISTS fact_order (
    order_id        BIGINT COMMENT 'Order ID',
    customer_id     BIGINT COMMENT 'FK -> dim_customer',
    product_id      BIGINT COMMENT 'FK -> dim_product',
    date_id         INT    COMMENT 'FK -> dim_date',
    region_id       BIGINT COMMENT 'FK -> dim_region',
    order_quantity  INT             COMMENT 'Quantity',
    order_amount    DECIMAL(18,2)   COMMENT 'Amount'
)
DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 10
PROPERTIES ("replication_num" = "1");

-- Summary: sales wide
CREATE TABLE IF NOT EXISTS dws_sales_wide (
    date_id        INT,
    region_name    VARCHAR(64),
    customer_name  VARCHAR(64),
    category       VARCHAR(64),
    brand          VARCHAR(64),
    total_amount   DECIMAL(20,2),
    total_quantity BIGINT,
    order_count    BIGINT
)
DUPLICATE KEY(date_id, region_name)
DISTRIBUTED BY HASH(date_id) BUCKETS 5
PROPERTIES ("replication_num" = "1");

-- Application: customer profile (RFM tags)
CREATE TABLE IF NOT EXISTS ads_customer_profile (
    customer_id        BIGINT,
    customer_name      VARCHAR(64),
    member_level       VARCHAR(16),
    total_orders       BIGINT,
    total_amount       DECIMAL(20,2),
    avg_order_amount   DECIMAL(20,2),
    preferred_category VARCHAR(64),
    rfm_segment        VARCHAR(32),
    is_high_value      BOOLEAN,
    is_churn_risk      BOOLEAN,
    lifecycle_stage    VARCHAR(32)
)
UNIQUE KEY(customer_id)
DISTRIBUTED BY HASH(customer_id) BUCKETS 5
PROPERTIES ("replication_num" = "1");
```

---

## 3. Metadata & Semantic Layer

The whole NL2SQL quality depends on metadata. AskInsight reads a `meta_config.yaml`
that describes each table and column **once**, and uses it for recall, alias matching,
SQL generation and governance.

```yaml
tables:
  - name: fact_order
    role: fact                      # dim | fact
    description: Order fact table; records core metrics.
    columns:
      - {name: order_id,    role: primary_key, description: Order ID., alias: ["order_id"]}
      - {name: customer_id, role: foreign_key, description: FK to customer., alias: ["customer_id"]}
      - {name: order_amount, role: measure,    description: Order amount., alias: ["order_amount"]}

metrics:
  - name: GMV
    description: Gross Merchandise Value.
    relevant_columns: [fact_order.order_amount]
    alias: ["gross_merchandise_value"]
  - name: AOV
    description: Average Order Value.
    relevant_columns: [fact_order.order_amount]
    alias: ["average_order_value"]
```

### 3.1 Auto-generate metadata from a live schema

If you already have the tables, generate the config instead of hand-writing it:

```bash
cd backend
python -m app.scripts.auto_bootstrap --db dw --output conf/meta_config.yaml
```

The bootstrapper scans the real schema, detects primary/foreign keys, infers roles,
and adds Chinese aliases from its built-in dictionary.

### 3.2 Semantic layers to fill before going live

| Layer | What to configure | Impact |
|------|------|------|
| Tables/columns | `meta_config.yaml` tables | recall + SQL generation |
| Metrics | `metrics` block with aliases | metric resolution |
| Vector store | Milvus collections (column/metric) | semantic search |
| Values | column values for dimension value matching | exact match of "East" etc. |

---

## 4. Infrastructure Setup

All 4 infra pieces are required. Use docker-compose or your existing deployments.

| Service | Default host | Port | Role |
|------|------|------|------|
| Apache Doris | 192.168.137.52 | 9030 | SQL execution |
| Milvus | 192.168.137.50 | 19530 | vector search |
| Redis | 192.168.137.51 | 6379 | cache + session |
| LLM (OpenAI-compatible) | LLM_BASE_URL | 3456 | NL2SQL / agents |

### 4.1 Environment variables (`.env`)

```bash
# Auth
JWT_SECRET=change-me-to-a-random-string
ADMIN_PASSWORD=change-me

# Doris
DORIS_HOST=192.168.137.52
DORIS_PASSWORD=<your-password>

# Milvus
MILVUS_HOST=192.168.137.50
MILVUS_PASSWORD=<your-password>

# Redis
REDIS_HOST=192.168.137.51
REDIS_PASSWORD=<your-password>

# LLM
LLM_API_KEY=<your-key>
LLM_MODEL_NAME=deepseek-v4-pro
LLM_BASE_URL=http://192.168.137.190:3456/v1

# Embedding
EMBEDDING_API_KEY=<your-key>
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 4.2 Model routing (multi-model, P3)

`backend/app/infra/llm_router.py` routes by task type with automatic fallback and
a 10-minute circuit breaker on failures:

| Task | Priority |
|------|------|
| intent / sql_simple | deepseek-v4-flash → deepseek-v4-pro |
| sql_complex / etl_gen | deepseek-v4-pro → kimi-k2.7-code |
| root_cause | deepseek-v4-pro → GLM-5.2 |
| free_form | deepseek-v4-pro → kimi-k2.7-code |

---

## 5. Data Loading (ODS → DWS → ADS)

Typical daily pipeline (Airflow / scheduled jobs):

```
1. ODS : import business DB tables (orders, customers, products, payments)
2. DWD : clean + dedup + add FK ids -> fact_order
3. DWS : aggregate by date/region/category/brand -> dws_sales_wide, dws_region_summary
4. ADS : customer profiling (RFM, churn risk) -> ads_customer_profile
5. Refresh: dim tables + column value info + Milvus vectors
```

Loading order matters: dimensions first, then facts, then summary/app tables.

---

## 6. Build the Knowledge Base

Run this once after the warehouse has data:

```bash
cd backend
python -m app.scripts.build_meta_knowledge
```

This populates:
- `data-agent-column` and `data-agent-metric` Milvus collections (1024-dim, via DashScope text-embedding-v3)
- column value info for dimension value exact matching
- glossary and lineage tables

### 6.1 Vector dimension

Embedding dimension is fixed at **1024** (`text-embedding-v3`). Keep
`milvus.embedding_size = 1024` in `backend/conf/app_config.yaml`.

---

## 7. Start the System

### Docker (all-in-one)

```bash
cp .env.example .env          # then edit .env
make up                       # docker compose up -d
make init                     # build knowledge base
# Frontend : http://localhost
# Backend  : http://localhost:8000/health
```

### Local dev (backend + frontend separately)

```bash
make dev
# backend  : uvicorn main:app --port 8000
# frontend : npm run dev (Vite)
```

---

## 8. End-to-End Usage

### 8.1 NL2SQL data query

> "What was sales by region last week?"

1. Intent recognition → `data_query`
2. Keywords: last week / each region / sales
3. RRF fusion recalls: `dws_sales_wide.total_amount`, dim `region_name`, dim `dim_date`
4. LLM generates 3 candidate SQL → safety check → complexity tier → execute
5. Result streamed as SSE → frontend renders table/chart

```sql
SELECT region_name, SUM(total_amount) AS total_amount
FROM dws_sales_wide
WHERE date_id >= 20260803 AND date_id <= 20260809
GROUP BY region_name
ORDER BY total_amount DESC;
```

### 8.2 Chitchat

> "Hello" → intent `chat` → friendly reply, no SQL.

### 8.3 Phase-4 agent intents (multi-agent orchestration)

The orchestrator (`/orchestrator/execute`) routes non-data-query intents:

| Intent | Primary agent | Mode |
|------|------|------|
| etl_request | etl_agent | sequential (etl → sql) |
| quality_check | governance_agent | single |
| alert_investigate / anomaly_explain | alert_root_cause_agent + governance_agent | parallel |
| metric_define | metric_agent | single |
| metadata_query | intent_agent | single |

---

## 9. Governance & Alerts

- **Anomaly detection**: Z-Score on query results; drill-down attribution pinpoints dimension
- **Lineage**: SQL lineage extraction upstream/downstream
- **Quality rules**: auto-generated for new ETL tables (row count drop / null rate / freshness)
- **Alert pipeline**: Prometheus / Airflow / custom webhooks → dedup (5-min fingerprint window) →
  correlation (10-min incident grouping) → root cause (evidence + LLM reasoning) → Runbook
- **Escalation**: unknown arbitration → human ticket + DingTalk P0 notification

---

## 10. Verification & Go-Live

### 10.1 Run the test suites

```bash
cd backend
pytest tests/ -q                     # full suite
pytest tests/unit tests/integration  # fast unit + integration
```

### 10.2 Agent readiness check

```bash
cd backend
python -m app.scripts.readiness_check
```

Admission scoring dimensions (pass ≥ 70):

| Dimension | Weight |
|------|------|
| Schema completeness | 25% |
| Relationship mapping | 25% |
| Business definitions | 20% |
| Agent readiness (smoke) | 20% |
| Security baseline | 10% |

---

## 11. Troubleshooting Cheat Sheet

| Symptom | Likely cause | Fix |
|------|------|------|
| `401` on protected endpoints | JWT_SECRET missing in shell | export JWT_SECRET / ADMIN_PASSWORD |
| Milvus search returns 0 | collection empty or dimension mismatch | re-run `build_meta_knowledge` |
| Embedding errors | EMBEDDING_API_KEY missing/wrong | verify DashScope key |
| SQL blocked | safety guard / whitelist | verify table is in meta_config + whitelist |
| Agent never runs | intent not matched | broaden `_KEYWORD_RULES` in query_integration |
| Kafka warnings on audit | Kafka down | falls back to Redis/file; not fatal |

---

## 12. What's Already Implemented (this repo)

| Phase | Scope | Where |
|------|------|------|
| P0–P2 | metadata, orchestrator, governance (semantic layer) | `app/agent`, `app/core` |
| P3 | LLM routing, prompts, tool registry, memory, planner | `app/infra` |
| P4 | SQL toolkit, metadata API, profiler, scheduler, notifier, log analyzer | `app/tools` |
| P5 | ETL recommendation, SQL gen, schedule, quality rules, review drafts | `app/agents/etl_agent` |
| P6 | alert ingestion, dedup, correlation, root cause, runbook | `app/alerts` + `app/agents/alert_root_cause_agent` |
| P1-08 | multi-agent orchestrator + REST + query-flow integration | `app/orchestrator` |

---

*See also: [中文版](DW-Implementation-Case-CN.md) · [Case Index](README.md) · [README](../README.md) · [API docs](http://localhost:8000/docs)*

## Appendix A. One-shot Demo Script

A runnable demo script is included: `backend/app/scripts/quickstart_demo.py`.

```bash
cd backend
python -m app.scripts.quickstart_demo --steps all            # create tables + seed + knowledge + query
python -m app.scripts.quickstart_demo --steps tables          # only DDL + seed
python -m app.scripts.quickstart_demo --steps knowledge       # only build knowledge base
python -m app.scripts.quickstart_demo --steps query --question "各地区的销售额是多少"
```

