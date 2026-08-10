# AskInsight — AI-Powered Data Insight Agent
[English](README.md) | [中文](docs/README_CN.md) | [日本語](docs/README_JP.md)


[![CI](https://github.com/your-org/askinsight/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Lang](https://img.shields.io/badge/lang-EN_%7C_CN-blue)]
[![Vue](https://img.shields.io/badge/vue-3.5-42b883.svg)](https://vuejs.org/)]

> Enterprise-grade NL2SQL intelligent data-query system — 24-node LangGraph workflow + Vue 3 frontend.

Converts natural-language queries into SQL, executes them automatically, and visualizes the results. Built specifically for Apache Doris data warehouses; supports Milvus vector retrieval + Redis caching.

---

## ✨ Core Features

| Module | Capability |
|------|------|
| **NL2SQL Engine** | 24-node workflow, 93.3% enterprise warehouse pass rate, 3-candidate SQL voting |
| **FK→PK Inference** | Auto-detect foreign-key relationships to generate REFERENCES, no manual annotation needed |
| **RRF Three-way Fusion** | Field + metric + dimension-value three-way recall, k=60 Reciprocal Rank Fusion |
| **Complexity Tiers** | SQL scoring routes: simple = fully automatic / medium = validated / complex = degraded |
| **Safety Guardrails** | 15 SQL-injection interceptors + table-level whitelist + forced LIMIT |
| **Data Governance** | SQL lineage + Z-Score anomaly detection + drill-down attribution + decision insights |
| **Frontend Visualization** | Auto-switching across 6 chart types + conditional coloring + CSV/Excel export |

---

## 🏗 Architecture

```
User question                     Frontend (Vue3 + ECharts)
  │ "Sales by brand"              │ SSE streaming render
  ▼                                ▼
┌──────────────────── Backend (FastAPI + LangGraph) ────────────────────┐
│                                                                       │
│  Intent recognition → Keywords → Recall (field/metric/value) → RRF fusion → Filter tables/metrics
│       │                                                               │
│       ▼                                                               │
│  Generate SQL (3 candidates) → Safety check → Complexity tier (3 levels) → Execute SQL
│       │                                                               │
│       ▼                                                               │
│  Lineage extraction → Anomaly detection → Decision insights → Python analysis
│                                                                       │
└──────────────────────────┬────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
      Apache Doris     Milvus          Redis
      (SQL execution)  (vector search) (cache/session)
```

---

## 🚀 Quick Start

### Option 1: One-click Docker deployment (recommended)

```bash
git clone https://github.com/your-org/askinsight.git
cd intelligent-decision-analytics

# 1. Configure environment variables
cp .env.example .env
# Edit .env — at minimum fill in LLM_API_KEY and EMBEDDING_API_KEY

# 2. Start all services
make up
# Or: docker compose up -d

# 3. Initialize the knowledge base (first run only)
make init

# 4. Access the app
# Frontend: http://localhost
# Backend: http://localhost:8000/health
```

### Option 2: Local development

```bash
# Backend
cd backend
pip install -e .
cp .env.example .env  # fill in your config
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# Frontend (in another terminal)
cd frontend
npm install
npm run dev  # http://localhost:5173
```

---

## 📁 Project Structure

```
intelligent-decision-analytics/
├── backend/                    # Python backend
│   ├── app/
│   │   ├── agent/              # LangGraph 24 nodes
│   │   │   ├── graph.py        # Graph definition
│   │   │   ├── nodes/          # Node implementations
│   │   │   └── state.py        # State definition
│   │   ├── api/                # FastAPI routes
│   │   ├── services/           # Business logic
│   │   ├── repositories/       # Data access (Doris/Milvus/PG)
│   │   ├── clients/            # Connection managers
│   │   └── core/               # Auth/cache/audit/rate-limit
│   ├── conf/                   # Configuration files
│   ├── prompts/                # LLM prompts
│   ├── tests/                  # Test cases
│   ├── mcp_server/             # MCP protocol server
│   ├── Dockerfile
│   └── pyproject.toml
│
├── frontend/                   # Vue 3 frontend
│   ├── src/
│   │   ├── App.vue
│   │   ├── views/              # Pages
│   │   ├── components/         # Components
│   │   └── router/
│   ├── Dockerfile
│   ├── nginx.conf              # Reverse-proxy config
│   └── package.json
│
├── docs/                       # Documentation
├── scripts/                    # Scripts
├── docker-compose.yml          # One-click deployment
├── Makefile                    # Shortcut commands
└── .env.example                # Environment variable template
```

---

## 🔧 Dependencies

| Service | Version | Purpose | Required |
|------|------|------|------|
| Apache Doris | 2.1+ | SQL execution engine | ✅ |
| Milvus | 2.4+ | Vector retrieval | ✅ |
| Redis | 7+ | Cache/session/rate-limit | ✅ |
| LLM API | any | OpenAI-compatible API | ✅ |
| Embedding API | DashScope | text-embedding-v3 | ✅ |
| PostgreSQL | 14+ | Multi-source data | ❌ |
| Kafka | 3.x | Audit log | ❌ |
| Superset | 4+ | Dashboard | ❌ |

---

## 📖 Documentation

- **🚀 Quick-Start Implementation Case (EN)** → [`docs/case/DW-Implementation-Case-EN.md`](docs/case/DW-Implementation-Case-EN.md)
- **🧭 Implementation Case Index** → [`docs/case/`](docs/case/README.md)
- **🚀 数仓实施案例（中文）** → [`docs/case/DW-Implementation-Case-CN.md`](docs/case/DW-Implementation-Case-CN.md)
- [Architecture Design](docs/architecture.md)
- [API Docs](http://localhost:8000/docs)
- [Configuration Guide](backend/conf/README.md)
- [Prompt Tuning](backend/prompts/)
- [Development Guide](docs/development.md)

---

## 🤝 Contributing

Issues and PRs welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
# Development workflow
git checkout -b feature/your-feature
make lint && make test
git commit -m "feat: your feature"
git push origin feature/your-feature
# Create a Pull Request
```

---

## 📄 License

[Apache License 2.0](LICENSE) — commercial use, modification, and distribution allowed.

---

## 🙏 Acknowledgements

- [LangGraph](https://github.com/langchain-ai/langgraph) - Workflow engine
- [Apache Doris](https://doris.apache.org/) - Analytical database
- [Milvus](https://milvus.io/) - Vector database
- [Vue.js](https://vuejs.org/) - Frontend framework
