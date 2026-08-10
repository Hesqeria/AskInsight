# 数仓实施案例 — AskInsight

> 企业级 NL2SQL Agent，构建于多层数仓之上（ODS → DWD → DWS → ADS）
> 本文档提供一个完整、可落地的实施过程，助你快速上手。

---

## 1. 业务场景

某零售电商企业建设分析型数仓。业务人员用中文/英文自然语言提问，系统自动生成 SQL、
执行并可视化结果，无需编写任何 SQL。

- **数仓分层**：ODS（原始）→ DWD（明细）→ DWS（汇总）→ ADS（应用）
- **维度**：地区 / 客户 / 商品 / 日期
- **核心指标**：GMV、客单价（AOV）、订单数、客户数
- **目标**：NL2SQL 数仓通过率 ≥93%，并具备完整治理闭环

---

## 2. 数仓架构

```
+-------------------------------------------------------------+
|  ADS   ads_customer_profile          （客户标签 / RFM）        |
|        dws_region_summary            （地区 KPI）              |
+-------------------------------------------------------------+
|  DWS   dws_sales_wide                （宽表事实，多维度）       |
+-------------------------------------------------------------+
|  DWD   fact_order                    （订单事实，3 个外键）     |
+-------------------------------------------------------------+
|  ODS   （从业务库导入的原始表）                                 |
+-------------------------------------------------------------+
|  DIM   dim_region / dim_customer / dim_product / dim_date     |
+-------------------------------------------------------------+
```

### 2.1 参考 DDL（Apache Doris）

```sql
-- 维度：地区
CREATE TABLE IF NOT EXISTS dim_region (
    region_id   BIGINT      COMMENT '地区ID',
    province    VARCHAR(64) COMMENT '省份',
    region_name VARCHAR(64) COMMENT '大区',
    country     VARCHAR(32) COMMENT '国家'
)
DUPLICATE KEY(region_id)
DISTRIBUTED BY HASH(region_id) BUCKETS 3
PROPERTIES ("replication_num" = "1");

-- 维度：日期
CREATE TABLE IF NOT EXISTS dim_date (
    date_id INT COMMENT 'yyyyMMdd',
    year    INT COMMENT '年',
    quarter INT COMMENT '季度',
    month   INT COMMENT '月',
    day     INT COMMENT '日'
)
UNIQUE KEY(date_id)
DISTRIBUTED BY HASH(date_id) BUCKETS 3
PROPERTIES ("replication_num" = "1");

-- 事实：订单（含 3 个外键）
CREATE TABLE IF NOT EXISTS fact_order (
    order_id        BIGINT COMMENT '订单ID',
    customer_id     BIGINT COMMENT '外键 -> dim_customer',
    product_id      BIGINT COMMENT '外键 -> dim_product',
    date_id         INT    COMMENT '外键 -> dim_date',
    region_id       BIGINT COMMENT '外键 -> dim_region',
    order_quantity  INT             COMMENT '数量',
    order_amount    DECIMAL(18,2)   COMMENT '金额'
)
DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 10
PROPERTIES ("replication_num" = "1");

-- 汇总：销售宽表
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

-- 应用：客户画像（RFM 标签）
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

## 3. 元数据与语义层

NL2SQL 的质量高度依赖元数据。AskInsight 通过一份 `meta_config.yaml` 描述每张表、
每个字段，**一处配置**，全局用于召回、别名匹配、SQL 生成与治理。

```yaml
tables:
  - name: fact_order
    role: fact                      # dim | fact
    description: 订单事实表，记录核心指标。
    columns:
      - {name: order_id,    role: primary_key, description: 订单ID., alias: ["订单ID", "order_id"]}
      - {name: customer_id, role: foreign_key, description: 关联客户维度., alias: ["客户ID", "用户ID"]}
      - {name: order_amount, role: measure,    description: 订单金额., alias: ["销售额", "订单金额"]}

metrics:
  - name: GMV
    description: 成交总额。
    relevant_columns: [fact_order.order_amount]
    alias: ["成交总额", "gross_merchandise_value"]
  - name: AOV
    description: 客单价。
    relevant_columns: [fact_order.order_amount]
    alias: ["平均单价", "average_order_value"]
```

### 3.1 从真实库结构自动生成元数据

如果表已存在，不必手写配置，直接生成：

```bash
cd backend
python -m app.scripts.auto_bootstrap --db dw --output conf/meta_config.yaml
```

引导器会扫描真实 schema，自动识别主键/外键、推断角色，并按内置词典补充中文别名。

### 3.2 上线前必须填充的语义层

| 层 | 配置内容 | 影响 |
|------|------|------|
| 表/字段 | `meta_config.yaml` 的 tables | 召回 + SQL 生成 |
| 指标 | metrics 块（含别名） | 指标解析 |
| 向量库 | Milvus 集合（字段/指标） | 语义检索 |
| 维度值 | 维度字段取值 | "华东"等精确匹配 |

---

## 4. 基础设施搭建

共需 4 个基础设施组件。可用 docker-compose，也可复用已有部署。

| 服务 | 默认地址 | 端口 | 作用 |
|------|------|------|------|
| Apache Doris | 192.168.137.52 | 9030 | SQL 执行 |
| Milvus | 192.168.137.50 | 19530 | 向量检索 |
| Redis | 192.168.137.51 | 6379 | 缓存 + 会话 |
| LLM（OpenAI 兼容） | LLM_BASE_URL | 3456 | NL2SQL / Agent |

### 4.1 环境变量（`.env`）

```bash
# 认证
JWT_SECRET=change-me-to-a-random-string
ADMIN_PASSWORD=change-me

# Doris
DORIS_HOST=192.168.137.52
DORIS_PASSWORD=<你的密码>

# Milvus
MILVUS_HOST=192.168.137.50
MILVUS_PASSWORD=<你的密码>

# Redis
REDIS_HOST=192.168.137.51
REDIS_PASSWORD=<你的密码>

# LLM
LLM_API_KEY=<你的key>
LLM_MODEL_NAME=deepseek-v4-pro
LLM_BASE_URL=http://192.168.137.190:3456/v1

# Embedding
EMBEDDING_API_KEY=<你的key>
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 4.2 多模型路由（P3）

`backend/app/infra/llm_router.py` 按任务类型路由，自动降级，失败 10 分钟熔断：

| 任务 | 优先级 |
|------|------|
| intent / sql_simple | deepseek-v4-flash → deepseek-v4-pro |
| sql_complex / etl_gen | deepseek-v4-pro → kimi-k2.7-code |
| root_cause | deepseek-v4-pro → GLM-5.2 |
| free_form | deepseek-v4-pro → kimi-k2.7-code |

---

## 5. 数据装载（ODS → DWS → ADS）

典型每日管道（Airflow / 定时任务）：

```
1. ODS : 导入业务库表（订单、客户、商品、支付）
2. DWD : 清洗 + 去重 + 补外键ID -> fact_order
3. DWS : 按日期/地区/品类/品牌汇总 -> dws_sales_wide, dws_region_summary
4. ADS : 客户画像（RFM、流失风险）-> ads_customer_profile
5. 刷新: 维度表 + 维度值 + Milvus 向量
```

装载顺序很重要：先维度，再事实，最后汇总/应用表。

---

## 6. 构建知识库

数仓有数据后执行一次：

```bash
cd backend
python -m app.scripts.build_meta_knowledge
```

填充内容：
- `data-agent-column` 与 `data-agent-metric` Milvus 集合（1024 维，DashScope text-embedding-v3）
- 维度值信息（用于维度值精确匹配）
- 术语与血缘表

### 6.1 向量维度

Embedding 固定为 **1024 维**（`text-embedding-v3`）。保持
`milvus.embedding_size = 1024` 于 `backend/conf/app_config.yaml`。

---

## 7. 启动系统

### Docker 一键部署

```bash
cp .env.example .env          # 然后编辑 .env
make up                       # docker compose up -d
make init                     # 构建知识库
# 前端 : http://localhost
# 后端 : http://localhost:8000/health
```

### 本地开发（前后端分别启动）

```bash
make dev
# 后端  : uvicorn main:app --port 8000
# 前端  : npm run dev（Vite）
```

---

## 8. 端到端使用

### 8.1 NL2SQL 数据查询

> "上周各地区的销售额是多少？"

1. 意图识别 → `data_query`
2. 关键词：上周 / 各地区 / 销售额
3. RRF 融合召回：`dws_sales_wide.total_amount`、维度 `region_name`、维度 `dim_date`
4. LLM 生成 3 个候选 SQL → 安全校验 → 复杂度分级 → 执行
5. 结果 SSE 流式返回 → 前端渲染表格/图表

```sql
SELECT region_name, SUM(total_amount) AS total_amount
FROM dws_sales_wide
WHERE date_id >= 20260803 AND date_id <= 20260809
GROUP BY region_name
ORDER BY total_amount DESC;
```

### 8.2 闲聊

> "你好" → 意图 `chat` → 友好回复，不查库。

### 8.3 Phase-4 Agent 意图（多 Agent 编排）

编排中枢（`/orchestrator/execute`）路由非数据查询意图：

| 意图 | 主 Agent | 模式 |
|------|------|------|
| etl_request | etl_agent | 顺序（etl → sql） |
| quality_check | governance_agent | 单 |
| alert_investigate / anomaly_explain | alert_root_cause_agent + governance_agent | 并行 |
| metric_define | metric_agent | 单 |
| metadata_query | intent_agent | 单 |

---

## 9. 治理与告警

- **异常检测**：查询结果 Z-Score 异常评分；下钻归因定位问题维度
- **血缘**：SQL 血缘提取，支持上游/下游追溯
- **质量规则**：新 ETL 表自动生成（行数骤降 / 空值率 / 新鲜度）
- **告警管道**：Prometheus / Airflow / 自定义 webhook → 去重（5 分钟指纹窗口）→
  关联（10 分钟故障聚合）→ 根因（证据 + LLM 推理）→ Runbook
- **升级**：无法自动仲裁 → 人工工单 + 钉钉 P0 通知

---

## 10. 验证与上线

### 10.1 运行测试套件

```bash
cd backend
pytest tests/ -q                    # 全量
pytest tests/unit tests/integration # 快速单测+集成
```

### 10.2 Agent 准入检查

```bash
cd backend
python -m app.scripts.readiness_check
```

准入评分维度（≥70 分通过）：

| 维度 | 权重 |
|------|------|
| Schema 完整性 | 25% |
| 关系映射 | 25% |
| 业务定义 | 20% |
| Agent 就绪（烟雾测试） | 20% |
| 安全基线 | 10% |

---

## 11. 常见问题速查

| 现象 | 可能原因 | 解决 |
|------|------|------|
| 受保护接口 `401` | shell 未设 JWT_SECRET | export JWT_SECRET / ADMIN_PASSWORD |
| Milvus 召回为空 | 集合为空或维度不一致 | 重新执行 `build_meta_knowledge` |
| Embedding 报错 | EMBEDDING_API_KEY 缺失/错误 | 校验 DashScope key |
| SQL 被拦截 | 安全守卫 / 白名单 | 确认表在 meta_config + 白名单 |
| Agent 不触发 | 意图未命中 | 在 query_integration 扩充 `_KEYWORD_RULES` |
| 审计 Kafka 告警 | Kafka 不可用 | 自动降级 Redis/文件，非致命 |

---

## 12. 本仓库已实现内容

| 期 | 范围 | 位置 |
|------|------|------|
| P0–P2 | 元数据、编排、治理（语义层） | `app/agent`、`app/core` |
| P3 | LLM 路由、提示词、工具注册、记忆、规划 | `app/infra` |
| P4 | SQL 工具、元数据 API、探查、调度、通知、日志 | `app/tools` |
| P5 | ETL 推荐、SQL 生成、调度、质量规则、审核草稿 | `app/agents/etl_agent` |
| P6 | 告警接入、去重、关联、根因、Runbook | `app/alerts` + `app/agents/alert_root_cause_agent` |
| P1-08 | 多 Agent 编排中枢 + REST + 主流程接入 | `app/orchestrator` |

---

*参考：[英文版](DW-Implementation-Case-EN.md) · [案例索引](README.md) · [README](../README.md) · [API 文档](http://localhost:8000/docs)*

## 附录 A. 一键演示脚本

内置可运行演示脚本：`backend/app/scripts/quickstart_demo.py`。

```bash
cd backend
python -m app.scripts.quickstart_demo --steps all            # 建表 + 造数 + 知识库 + 问答
python -m app.scripts.quickstart_demo --steps tables          # 仅建表 + 造数
python -m app.scripts.quickstart_demo --steps knowledge       # 仅构建知识库
python -m app.scripts.quickstart_demo --steps query --question "各地区的销售额是多少"
```

