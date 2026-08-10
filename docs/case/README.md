# 数仓实施案例集 / Implementation Case Library

> 面向快速开始实施的资料索引。中英双语案例文档 + 一键演示脚本。

## 文档 / Documents

| 文件 | 语言 | 内容 |
|------|------|------|
| [DW-Implementation-Case-EN.md](DW-Implementation-Case-EN.md) | English | Full warehouse implementation walkthrough |
| [DW-Implementation-Case-CN.md](DW-Implementation-Case-CN.md) | 中文 | 完整数仓实施案例（含 DDL/配置/问答演示） |

## 一键演示脚本 / One-shot Demo Script

`backend/app/scripts/quickstart_demo.py` 可在**已有基础设施**（Doris/Milvus/Redis/LLM）下
一键完成：建表 → 造数 → 构建知识库 → 跑通问答。

```bash
cd backend

# 全部步骤：建表 + 造数 + 知识库 + 问答
python -m app.scripts.quickstart_demo --steps all

# 只建表造数
python -m app.scripts.quickstart_demo --steps tables

# 只构建知识库（基于 conf/meta_config.yaml）
python -m app.scripts.quickstart_demo --steps knowledge

# 自定义问题跑问答
python -m app.scripts.quickstart_demo --steps query --question "各地区的销售额是多少"
```

### 前置条件 / Prerequisites

1. 已启动 Doris / Milvus / Redis / LLM 四件套，并正确配置 `.env`
2. 已准备 `backend/conf/meta_config.yaml`（可用 `auto_bootstrap` 自动生成）
3. 已安装 Python 依赖：`pip install -r backend/requirements.txt`

### 演示流程 / Pipeline

| 步骤 | 动作 | 产出 |
|------|------|------|
| 1 tables | 建 7 张表（4 维度 + 2 事实 + 1 画像） | Doris 表结构 |
| 2 seed | 灌入 20 条订单 + 维度/汇总数据 | 演示数据 |
| 3 knowledge | 同步 meta_config → Doris + Milvus 向量 | 知识库 |
| 4 query | 自然语言 → NL2SQL → 结果 | SSE 流式答案 |

> 提示：`--steps all` 会依次执行 1→2→3→4。重复执行是幂等的（CREATE IF NOT EXISTS）。
