-- 统一异步交互 Inbox (PRD 05-DeepSeek-Harness研究 M3)
-- 澄清/审批/未来一切交互点共用一张表:图挂起时写 inbox(next_step),
-- 恢复时按 kind 路由到处理器,基于 M1 事件日志 checkpoint 重建状态。
--
-- Run on the `data_agent` database.

CREATE TABLE IF NOT EXISTS agent_inbox (
    id          VARCHAR(64)  NOT NULL COMMENT 'inbox ID(主键)',
    session_id  VARCHAR(64)  NOT NULL COMMENT '关联会话(request_id)',
    channel     VARCHAR(16)  NOT NULL DEFAULT 'next_step'
                            COMMENT 'next_step(当前轮挂起) / next_turn(排队下一轮)',
    kind        VARCHAR(32)  NOT NULL COMMENT '交互类型: clarify|approval|...(处理器注册名)',
    payload     STRING       NULL     COMMENT 'JSON: {ref_id, resume_node, question, ...}',
    status      VARCHAR(16)  NOT NULL DEFAULT 'pending'
                            COMMENT 'pending/claimed/done/discarded',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at  DATETIME     NULL,
    resolved_at DATETIME     NULL
)
UNIQUE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 3
PROPERTIES("replication_num" = "1");
