-- 会话事件日志表 (PRD 05-DeepSeek-Harness研究 M1)
-- 事件日志 = 唯一真源:审计/重放/恢复/评测共享同一份 append-only 流。
-- 对齐 dsh "Model-visible means logged" 不变量:关键节点输出全部落事件。
--
-- 月分区(dynamic partition):自动预留未来 3 个月、保留历史 36 个月,
-- 避免单 tablet 无限膨胀(PRD M1 非功能需求:日志表按月分区)。
-- 注:分区列必须进 UNIQUE KEY,故为 (session_id, seq, created_at);
-- (session_id, seq) 的业务唯一性由 SDK 内存计数器保证,不受影响。
--
-- Run on the `data_agent` database.

CREATE TABLE IF NOT EXISTS session_event (
    session_id  VARCHAR(64)  NOT NULL COMMENT '会话ID(request_id)',
    seq         INT          NOT NULL COMMENT '会话内单调序号',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件时间(分区列,必须在 key 前缀内)',
    type        VARCHAR(48)  NOT NULL COMMENT '事件类型: query/received|intent/resolved|plan/built|plan/clarified|sql/generated|sql/validated|guard/decision|sql/executed|turn/ended|inbox/*|context/pressure|llm/retry|approval/*',
    payload     STRING       NULL     COMMENT '事件载荷 JSON(深拷贝只读)'
)
UNIQUE KEY(session_id, seq, created_at)
PARTITION BY RANGE(created_at) ()
DISTRIBUTED BY HASH(session_id) BUCKETS 3
PROPERTIES(
    "replication_num" = "1",
    "dynamic_partition.enable" = "true",
    "dynamic_partition.time_unit" = "MONTH",
    "dynamic_partition.start" = "-36",
    "dynamic_partition.end" = "3",
    "dynamic_partition.prefix" = "p",
    "dynamic_partition.buckets" = "3"
);

-- 存量部署迁移(表已存在时手工执行;新库直接用上面的建表语句):
--   ALTER TABLE session_event SET (
--     "dynamic_partition.enable" = "true",
--     "dynamic_partition.time_unit" = "MONTH",
--     "dynamic_partition.start" = "-36",
--     "dynamic_partition.end" = "3",
--     "dynamic_partition.prefix" = "p",
--     "dynamic_partition.buckets" = "3"
--   );
-- 注:UNIQUE KEY 若需加入 created_at 列需重建表;未迁移的存量表
-- (UNIQUE(session_id, seq))功能不受影响,只是暂无分区老化。
