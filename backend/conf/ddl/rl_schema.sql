-- RL (Thompson Sampling) tables for decision_insight policy learning.
-- Run on the `data_agent` database (same as feedback_log / anomaly_event).

CREATE TABLE IF NOT EXISTS rl_policy_stats (
    policy_name      VARCHAR(64)  NOT NULL COMMENT '策略名 (facts_first / action_first / risk_focus / executive)',
    alpha            DOUBLE       DEFAULT 1.0 COMMENT 'Beta 分布正例参数 (positive+1)',
    beta             DOUBLE       DEFAULT 1.0 COMMENT 'Beta 分布负例参数 (negative+1)',
    total_calls      BIGINT       DEFAULT 0   COMMENT '总调用次数',
    positive_rewards BIGINT       DEFAULT 0   COMMENT '正反馈数 (rating=1)',
    negative_rewards BIGINT       DEFAULT 0   COMMENT '负反馈数 (rating=0)',
    last_updated     DATETIME     DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(policy_name)
DISTRIBUTED BY HASH(policy_name) BUCKETS 3
PROPERTIES("replication_num" = "1");

CREATE TABLE IF NOT EXISTS rl_decision_log (
    id              VARCHAR(64)  NOT NULL COMMENT '决策日志 ID',
    request_id      VARCHAR(64)             COMMENT '关联 request_id (quality rating 反查用)',
    policy_name     VARCHAR(64)             COMMENT '本次选用的策略',
    query           VARCHAR(500)            COMMENT '用户问题',
    sampled_score   DOUBLE                  COMMENT 'Beta 采样值 (0-1, 越大被选中概率越高)',
    insight         STRING                  COMMENT '生成的决策建议 (用于 DPO 数据导出)',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 3
PROPERTIES("replication_num" = "1");
