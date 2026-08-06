# AskInsight — AI 驱动的数据洞察智能体

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

> 企业级 NL2SQL 智能问数系统 — 将自然语言转为 SQL，自动执行并可视化。
> 专为 Apache Doris 数仓设计，支持 MySQL / PostgreSQL 多数据源。

---

## ✨ 核心特性

| 模块 | 能力 |
|------|------|
| **NL2SQL 引擎** | 24 节点 LangGraph 工作流，93.3% 企业数仓通过率 |
| **FK→PK 智能推断** | 自动识别外键关系，无需手工标注 |
| **RRF 三路融合召回** | 字段 + 指标 + 维度值检索，k=60 |
| **复杂度分级路由** | 3 级 SQL 评分：全自动 / 校验 / 降级 |
| **项目准入评分** | 5 维度强制评分，<70 分锁定查询 |
| **安全防护** | 15 种 SQL 注入拦截 + 路径白名单 + 沙箱 |
| **多数据源** | Doris / MySQL / PostgreSQL + 方言适配 |
| **Agent 烟雾测试** | DB / Schema / 查询 / 嵌入 / LLM 一键检测 |

---

## 🏗 架构

```
用户: "各品牌在各地区的销售额"
  │
  ▼
意图识别 → 关键字 → 召回(字段/指标/值) → RRF三路融合(k=60)
  │
  ▼
过滤表/指标 → 术语匹配 → 维度值注入 → 生成SQL(3候选投票)
  │
  ▼
安全校验(15种拦截) → 复杂度评估(3级) → 执行SQL
  │
  ▼
血缘提取 → 异常检测 → 决策洞察 → Python分析
```

---

## 🚀 快速开始

```bash
git clone https://github.com/Hesqeria/AskInsight.git
cd AskInsight
cp .env.example .env        # 填入 LLM_API_KEY 等
docker compose up -d        # 一键启动全部服务
make init                   # 初始化知识库
```

访问 http://localhost （前端）或 http://localhost:8000/docs （API文档）

---

## 📁 项目结构

```
AskInsight/
├── backend/               # Python 后端
│   ├── app/agent/         # 24 节点 LangGraph
│   ├── app/api/           # FastAPI 路由(9个)
│   ├── app/core/          # 安全/审计/方言/准入
│   └── prompts/           # 7 个 LLM 提示词
├── frontend/              # Vue3 + ECharts6
├── docker-compose.yml     # 一键部署
└── docs/                  # 文档
```

---

## 🔧 准入评分体系

| 维度 | 权重 | 说明 |
|------|------|------|
| Schema Completeness | 25% | 表结构完整性 |
| Relationship Mapping | 25% | FK→PK 映射关系 |
| Business Definitions | 20% | 字段描述 + 别名 |
| Agent Readiness | 20% | 6项烟雾测试 |
| Security Baseline | 10% | 安全白名单 |

总分 ≥ 70 且各维度达标 → 系统启用

---

## 📖 文档

- [API 文档](http://localhost:8000/docs)
- [英文 README](../README.md)
- [企业场景分析](https://github.com/Hesqeria/AskInsight/blob/main/docs/)
