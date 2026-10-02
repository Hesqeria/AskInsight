# DDL 部署说明 (data_agent 库)

本次 DeepSeek-Harness 落地新增了 3 张表 + 1 个列迁移。统一用一条命令应用:

```bash
cd backend
python -m app.scripts.apply_p2_ddl            # 实际应用
python -m app.scripts.apply_p2_ddl --check    # 预检:解析并打印执行计划,不写库
```

脚本按序幂等执行,重复运行安全(已存在的表/列自动跳过)。

## 本次新增

| 文件 | 表/变更 | 模块 | 说明 |
| --- | --- | --- | --- |
| `session_event_schema.sql` | `session_event` | M1 | 会话事件日志(唯一真源);月分区 dynamic partition |
| `agent_inbox_schema.sql` | `agent_inbox` | M3 | 统一异步交互收件箱(澄清/审批/未来交互) |
| `result_spill_schema.sql` | `result_spill` | M10 | 大结果溢写(TTL 7 天) |
| `clarify_add_request_id.sql` | `clarify_session` 加列 | M1 | `request_id` 关联原会话时间线 |

## 兼容性说明

- `clarify_add_request_id` 是 **ALTER**,老库未执行时后端会**自动降级**:
  - `ClarifyRepository.create` 探测列存在与否,缺失则用旧列清单插入;
  - `get_request_id` 探测后缺失返回空串,恢复流不强制归属。
  因此**旧库不迁移也能跑**,只是澄清恢复流的 M1 事件时间线无法跨轮归属原会话。
- `session_event` 月分区:分区列进 UNIQUE KEY `(session_id, seq, created_at)`。
  存量旧表(若已按旧版 `UNIQUE(session_id, seq)` 建过)功能不受影响,仅无分区老化;
  如需升级需重建表(见 schema 文件内注释)。

## 历史 DDL(已存在,无需重复应用)

Phase-4 之前的表(ontology/approval/rl/clarify)由 `app/scripts/apply_phase4_ddl.py` 管理,
本次不重复。

## 验证

应用后可用 `--check` 或直接查询确认:

```sql
SELECT COUNT(*) FROM information_schema.tables
WHERE table_schema = 'data_agent'
  AND table_name IN ('session_event', 'agent_inbox', 'result_spill');
-- 期望 3
```
