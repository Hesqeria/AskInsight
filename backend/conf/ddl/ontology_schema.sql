-- Ontology + Lineage schema (Phase 4 本体与数据血缘 PRD).
-- Run on the `data_agent` database (same as feedback_log / rl_*).
--
-- 7 tables:
--   ont_class       本体类层级
--   ont_property    类属性
--   ont_relation    类间关系 (替代旧 entity_relation, 旧表保留为视图)
--   ont_instance    物理 db.table.column -> 类/属性映射
--   lineage_technical  永久技术血缘 (表/列/转换)
--   lineage_business   业务术语 -> 指标 -> 物理字段
--   lineage_semantic   本体推理产生的语义血缘
--
-- Compatibility: Doris 2.x.  Uses UNIQUE KEY model for point lookups.
-- Replication factor 1 (single-node dev / CI); production should bump
-- to 2-3 via ALTER TABLE.

-- ============================================================ #
-- 1. ont_class - 类层级
-- ============================================================ #
CREATE TABLE IF NOT EXISTS ont_class (
    class_id        VARCHAR(64)  NOT NULL COMMENT '类ID (C001/C010...)',
    class_name      VARCHAR(64)  NOT NULL COMMENT '英文类名 Customer/Product',
    class_name_zh   VARCHAR(64)  NOT NULL COMMENT '中文类名 客户/商品',
    parent_class_id VARCHAR(64)  NULL     COMMENT '父类ID (支持类层级)',
    description     STRING       NULL,
    is_transitive   BOOLEAN      NOT NULL DEFAULT FALSE COMMENT '层级关系是否传递 (品类/地域)',
    pii_level       TINYINT      NOT NULL DEFAULT 0 COMMENT 'PII 等级 0-3 (3=最敏感)',
    owner           VARCHAR(64)  NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(class_id)
DISTRIBUTED BY HASH(class_id) BUCKETS 3
PROPERTIES("replication_num" = "1");

-- ============================================================ #
-- 2. ont_property - 类属性
-- ============================================================ #
CREATE TABLE IF NOT EXISTS ont_property (
    property_id      VARCHAR(64)  NOT NULL,
    property_name    VARCHAR(64)  NOT NULL COMMENT '属性名 member_level',
    property_name_zh VARCHAR(64)  NOT NULL COMMENT '中文 会员等级',
    class_id         VARCHAR(64)  NOT NULL COMMENT '所属类',
    data_type        VARCHAR(32)  NULL,
    is_pk            BOOLEAN      NOT NULL DEFAULT FALSE,
    is_fk            BOOLEAN      NOT NULL DEFAULT FALSE,
    is_pii           BOOLEAN      NOT NULL DEFAULT FALSE,
    business_term    VARCHAR(128) NULL     COMMENT '关联业务术语表 term_name',
    description      STRING       NULL,
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(property_id)
DISTRIBUTED BY HASH(property_id) BUCKETS 3
PROPERTIES("replication_num" = "1");

-- ============================================================ #
-- 3. ont_relation - 类间关系 (替代 entity_relation)
-- ============================================================ #
CREATE TABLE IF NOT EXISTS ont_relation (
    relation_id      VARCHAR(128) NOT NULL COMMENT '关系ID src_class:rel_type:dst_class',
    src_class_id     VARCHAR(64)  NOT NULL,
    dst_class_id     VARCHAR(64)  NOT NULL,
    relation_type    VARCHAR(64)  NOT NULL COMMENT 'is_a/belongs_to/parent_of/...',
    relation_type_zh VARCHAR(64)  NULL,
    cardinality      VARCHAR(8)   NOT NULL DEFAULT 'N:1' COMMENT '1:1/1:N/N:1/N:N',
    is_transitive    BOOLEAN      NOT NULL DEFAULT FALSE COMMENT '传递关系 (parent_of)',
    is_symmetric     BOOLEAN      NOT NULL DEFAULT FALSE COMMENT '对称关系 (related_to)',
    description      STRING       NULL,
    confidence       DOUBLE       NOT NULL DEFAULT 1.000 COMMENT '置信度 LLM推理<1',
    source           VARCHAR(16)  NOT NULL DEFAULT 'MANUAL' COMMENT 'MANUAL/LLM/SQL_PARSE',
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(relation_id)
DISTRIBUTED BY HASH(relation_id) BUCKETS 3
PROPERTIES("replication_num" = "1");

-- ============================================================ #
-- 4. ont_instance - 物理字段 -> 本体映射
-- ============================================================ #
CREATE TABLE IF NOT EXISTS ont_instance (
    instance_id  VARCHAR(128) NOT NULL COMMENT 'db.table.column 完整路径',
    db_name      VARCHAR(32)  NOT NULL,
    table_name   VARCHAR(64)  NOT NULL,
    column_name  VARCHAR(64)  NULL     COMMENT '列级映射; 表级则空',
    class_id     VARCHAR(64)  NOT NULL,
    property_id  VARCHAR(64)  NULL,
    role         VARCHAR(16)  NULL     COMMENT 'PK/FK/MEASURE/DIMENSION/NULL',
    confidence   DOUBLE       NOT NULL DEFAULT 1.000,
    source       VARCHAR(16)  NOT NULL DEFAULT 'MANUAL',
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(instance_id)
DISTRIBUTED BY HASH(instance_id) BUCKETS 5
PROPERTIES("replication_num" = "1");

-- ============================================================ #
-- 5. lineage_technical - 永久技术血缘 (查询级 sql_lineage 仍保留)
-- ============================================================ #
CREATE TABLE IF NOT EXISTS lineage_technical (
    lineage_id     VARCHAR(128) NOT NULL COMMENT 'src_col->dst_col',
    src_db         VARCHAR(32)  NOT NULL,
    src_table      VARCHAR(64)  NOT NULL,
    src_column     VARCHAR(64)  NULL,
    dst_db         VARCHAR(32)  NOT NULL,
    dst_table      VARCHAR(64)  NOT NULL,
    dst_column     VARCHAR(64)  NULL,
    transformation VARCHAR(32)  NOT NULL COMMENT 'DIRECT/JOIN/AGG/FILTER/CASE/UNION/DERIVED',
    transform_expr STRING       NULL,
    etl_task_id    VARCHAR(64)  NULL,
    confidence     DOUBLE       NOT NULL DEFAULT 1.000,
    source         VARCHAR(16)  NOT NULL DEFAULT 'SQL_PARSE',
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(lineage_id)
DISTRIBUTED BY HASH(lineage_id) BUCKETS 5
PROPERTIES("replication_num" = "1");

-- ============================================================ #
-- 6. lineage_business - 业务术语/指标 -> 物理字段
-- ============================================================ #
CREATE TABLE IF NOT EXISTS lineage_business (
    business_id     VARCHAR(64)  NOT NULL,
    term_name       VARCHAR(128) NOT NULL COMMENT '业务术语 GMV/复购率',
    metric_id       VARCHAR(64)  NULL     COMMENT '关联 metric_info.id',
    sql_template    STRING       NULL,
    related_tables  STRING       NULL     COMMENT 'JSON 数组',
    related_columns STRING       NULL     COMMENT 'JSON 数组',
    filters         STRING       NULL,
    owner           VARCHAR(64)  NULL,
    description     STRING       NULL,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(business_id)
DISTRIBUTED BY HASH(business_id) BUCKETS 3
PROPERTIES("replication_num" = "1");

-- ============================================================ #
-- 7. lineage_semantic - 本体推理产生的语义血缘
-- ============================================================ #
CREATE TABLE IF NOT EXISTS lineage_semantic (
    semantic_id     VARCHAR(128) NOT NULL,
    src_class_id    VARCHAR(64)  NOT NULL,
    src_instance_id VARCHAR(128) NULL     COMMENT '具体实例 activity_id=1001',
    dst_class_id    VARCHAR(64)  NOT NULL,
    relation_path   STRING       NULL     COMMENT '推理路径 Order->promoted_by->Campaign',
    impact_type     VARCHAR(32)  NOT NULL COMMENT 'CASCADE/RESTRICT/AGGREGATE',
    impact_desc     STRING       NULL,
    confidence      DOUBLE       NOT NULL DEFAULT 1.000,
    source          VARCHAR(16)  NOT NULL DEFAULT 'INFERENCE',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
)
UNIQUE KEY(semantic_id)
DISTRIBUTED BY HASH(semantic_id) BUCKETS 3
PROPERTIES("replication_num" = "1");
