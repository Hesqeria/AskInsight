-- Approval tickets for the PII gate workflow (Phase 4 dev-prd/P1-04).
--
-- A ticket is created when a generated/corrected SQL references PII L>=3
-- columns (per the ontology policy). The ticket holds the full agent
-- state at the moment the gate triggered, so the SQL can be resumed
-- from `execute_sql` once an approver says yes (or rolled back on no).
--
-- Run on the `data_agent` database.

CREATE TABLE IF NOT EXISTS approval_ticket (
    ticket_id      VARCHAR(64)  NOT NULL COMMENT 'UUID',
    request_id     VARCHAR(64)  NOT NULL COMMENT '关联 query request_id',
    username       VARCHAR(64)  NULL     COMMENT '发起 SQL 的用户',
    sql_text       STRING       NOT NULL COMMENT '待审批的 SQL',
    pii_reason     STRING       NULL     COMMENT 'PIE gate 触发原因',
    pii_violations STRING       NULL     COMMENT 'JSON 列表 [{column, class, level}]',
    status         VARCHAR(16)  NOT NULL DEFAULT 'pending'
                              COMMENT 'pending / approved / rejected / expired',
    decided_by     VARCHAR(64)  NULL,
    decided_at     DATETIME     NULL,
    decision_note  STRING       NULL,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(ticket_id)
DISTRIBUTED BY HASH(ticket_id) BUCKETS 3
PROPERTIES("replication_num" = "1");

-- Index-equivalent for request_id lookups (Doris DUPLICATE KEY already
-- covers point lookups via the ticket_id hash, so no extra index needed
-- at this scale).
