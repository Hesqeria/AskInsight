-- 大结果溢写表 (PRD 05-DeepSeek-Harness研究 M10)
-- execute_sql 结果超过阈值(默认 500 行 / 256KB)时全量落表,模型面/前端
-- 只拿 head+tail 预览 + spill_id;查看全量走 /api/v1/spills/{id} 分页。
-- TTL 7 天由后台清扫任务回收。
--
-- Run on the `data_agent` database.

CREATE TABLE IF NOT EXISTS result_spill (
    spill_id    VARCHAR(64)  NOT NULL COMMENT '溢写ID(主键)',
    session_id  VARCHAR(64)  NOT NULL COMMENT '会话ID(request_id)',
    sql_hash    VARCHAR(64)  NOT NULL COMMENT 'SQL MD5(审计/去重)',
    sql_text    STRING       NULL     COMMENT '截断后的 SQL 摘要(审计)',
    rows_json   STRING       NOT NULL COMMENT '全量结果 JSON(已脱敏,rows 为保留字故改名)',
    row_count   INT          NOT NULL COMMENT '行数',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(spill_id)
DISTRIBUTED BY HASH(spill_id) BUCKETS 3
PROPERTIES("replication_num" = "1");
