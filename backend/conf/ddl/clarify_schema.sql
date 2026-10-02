-- 模糊提问澄清会话表 (模糊提问澄清交互-PRD.md)
-- 镜像 approval_ticket 结构,持久化澄清流程的中止-恢复数据
-- Run on the `data_agent` database.

CREATE TABLE IF NOT EXISTS clarify_session (
    clarify_id        VARCHAR(64)  NOT NULL COMMENT '澄清会话ID(主键)',
    question          STRING       NULL     COMMENT '用户原始 NL 问题',
    initial_plan      STRING       NULL     COMMENT '初始 SemanticPlan JSON',
    final_plan        STRING       NULL     COMMENT '用户确认后的 plan JSON',
    missing_fields    STRING       NULL     COMMENT '缺失字段 JSON [{field, reason, current_value}]',
    suggestions_given STRING       NULL     COMMENT '给出的候选选项 JSON [{field, prompt, options}]',
    user_response     STRING       NULL     COMMENT '用户响应 JSON',
    status            VARCHAR(16)  NOT NULL DEFAULT 'pending'
                                COMMENT 'pending/confirmed/amended/abandoned/expired',
    rounds            INT          NOT NULL DEFAULT '1' COMMENT '已澄清轮次',
    username          VARCHAR(64)  NULL,
    request_id        VARCHAR(64)  NULL COMMENT '原会话 request_id(session_event.session_id)',
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at       DATETIME     NULL,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(clarify_id)
DISTRIBUTED BY HASH(clarify_id) BUCKETS 3
PROPERTIES("replication_num" = "1");
