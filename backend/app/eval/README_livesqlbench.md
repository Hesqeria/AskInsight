# LiveSQLBench 评测接入

在 **SQLite 版 LiveSQLBench** 上评测我们 Agent/LLM 的 NL2SQL 竞争力,无需
Docker / PostgreSQL,直接用 Python 标准库 `sqlite3` 执行。

## 一、当前状态

| 组件 | 文件 | 状态 |
| --- | --- | --- |
| SQLite 执行后端 | `app/eval/evaluator/sqlite_ex.py` | ✅ |
| 数据加载器 | `app/eval/dataset/livesqlbench_loader.py` | ✅ |
| 评测入口 | `app/eval/run_livesqlbench.py` | ✅ |
| 单元测试 | `tests/unit/test_livesqlbench.py`(34 个) | ✅ |
| 烟雾测试 | mock 2/2 + llm 1/1 | ✅ |

## 二、数据接入(数据就位后即可跑)

数据集:HuggingFace `birdsql/livesqlbench-base-lite-sqlite`
(约几百 MB,含 18 个 SQLite 库 + 题目 + sol_sql)

```
git clone https://huggingface.co/datasets/birdsql/livesqlbench-base-lite-sqlite
# 或下载 zip 解压到本地目录,例如: D:/datasets/livesqlbench-base-lite-sqlite
```

预期目录结构:
```
<data_root>/
  livesqlbench_data.jsonl      # 题目(含 sol_sql 的完整版需向作者邮件获取)
  databases/
    <db_name>.db               # SQLite 数据库文件
    <db_name>/
      column_meaning.json      # 列含义(可选,自动加载)
      hkb/                     # 分层知识库(可选,自动加载)
```

> 注意:公开版 `livesqlbench_data.jsonl` 不含 `sol_sql` / `test_cases`。
> 如需完整版,向 bird.bench25@gmail.com 发邮件,标题带
> `[livesqlbench-base-lite-SQLite GT&Test Cases]`,自动回复约 30 分钟内到达。
> 收到后将 sol_sql 合入 jsonl 即可。

## 三、运行评测

```bash
# 1) LLM 直调评测(推荐,反映模型竞争力)
cd AskInsight/backend
python -m app.eval.run_livesqlbench \
    --data-root D:/datasets/livesqlbench-base-lite-sqlite \
    --adapter llm --limit 50

# 2) 只测 SELECT Query 类
python -m app.eval.run_livesqlbench \
    --data-root <path> --adapter llm --category Query

# 3) Mock(框架自检,应 100% 通过)
python -m app.eval.run_livesqlbench \
    --data-root <path> --adapter mock --limit 10
```

输出:报告写入 `app/eval/runs/livesqlbench_<run_id>.{md,json}`。

## 四、评分逻辑(对齐官方 Soft EX)

1. **预处理**(`sqlite_ex.preprocess_sql_for_compare`):
   - 去注释、降级 `SELECT DISTINCT` → `SELECT`
   - ordered 比较保留 `ORDER BY`;无序比较去除
2. **执行**:pred_sql 与 sol_sql 都在只读 SQLite 会话执行
3. **规范化**(`normalize_rows`):数值 round 2 位、`None`/`''` 统一、bool→int
4. **比较**:复用 `app.eval.utils.result_sets_equal`(有序或无序)

> 与官方差异:官方用 PostgreSQL + 复杂 test_cases(CRUD 校验、QEP 性能对比)。
> 本框架覆盖 **SELECT Query 类的 Soft EX**;Management(CRUD)类暂用
> 执行成功 + 结果等价近似判定,完整 test_cases 校验需 Docker/PG。

## 五、常见问题

| 问题 | 解决 |
| --- | --- |
| 找不到 jsonl | 检查数据目录,确认 `livesqlbench_data.jsonl` 存在 |
| 找不到 db | 确认 `databases/<db>.db` 命名,loader 自动递归查找 |
| sol_sql 为空 | 用公开版无 sol_sql,需邮件获取完整版 |
| LLM 生成慢 | 加 `--limit`,或并行(后续支持) |

## 六、后续增强(数据就位后可选)

- [ ] 并行评测(concurrency)
- [ ] Management(CRUD)题目的 test_cases 校验
- [ ] 接入我们 Agent(RAG + 本体)而非裸 LLM,对比"裸 LLM vs 全链路 Agent"
- [ ] 与自建 100 题基线(EX 31%)并列报告
