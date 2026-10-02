-- 澄清会话反馈回流表 (模糊提问澄清交互-PRD.md P3-CLARIFY-15)
-- 把每个澄清会话的 (原始问题, 缺失字段, 用户答案, 最终plan, 轮次, 结果)
-- 落库,作为 RL / DPO 微调的训练样本数据源。
-- Run on the `data_agent` database.

CREATE TABLE IF NOT EXISTS clarify_feedback (
    id                 BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    clarify_id         VARCHAR(64)  NOT NULL COMMENT '关联 clarify_session.clarify_id',
    question           STRING       NULL     COMMENT '用户原始 NL 问题',
    initial_confidence FLOAT        NULL     COMMENT '初始 plan 置信度',
    missing_fields     STRING       NULL     COMMENT '缺失字段 JSON',
    user_response      STRING       NULL     COMMENT '用户答案 JSON (selections/free_text)',
    final_plan         STRING       NULL     COMMENT '合并后的最终 plan JSON',
    final_confidence   FLOAT        NULL     COMMENT '合并后置信度',
    rounds             INT          NOT NULL DEFAULT '1' COMMENT '澄清轮次',
    outcome            VARCHAR(16)  NOT NULL DEFAULT 'confirmed'
                                 COMMENT 'confirmed/amended/abandoned/forced',
    username           VARCHAR(64)  NULL,
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 3
PROPERTIES("replication_num" = "1");
