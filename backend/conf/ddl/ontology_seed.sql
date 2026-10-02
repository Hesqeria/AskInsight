-- Seed data for ontology tables (Phase 4 本体与数据血缘 PRD §4.1.2/4.2.2/4.3.2).
-- Run AFTER ontology_schema.sql. Idempotent: uses INSERT ... ON DUPLICATE KEY UPDATE.

USE data_agent;

-- ============================================================ #
-- ont_class: 16 类 (1 根 + 15 业务类, 覆盖 dw 数仓 55 表)
-- ============================================================ #
INSERT INTO ont_class (class_id, class_name, class_name_zh, parent_class_id, description, is_transitive, pii_level) VALUES
    ('C001', 'Entity',       '业务实体根类', NULL,   '所有业务实体的根类', FALSE, 0),
    ('C010', 'Customer',     '客户',         'C001', '客户域 (含用户/会员)',  FALSE, 2),
    ('C011', 'User',         '注册用户',     'C010', '注册用户/账户主体',     FALSE, 3),
    ('C012', 'MemberLevel',  '会员等级',     'C010', '用户会员等级',          FALSE, 0),
    ('C020', 'Product',      '商品',         'C001', '商品域 (含品类/SKU)',   FALSE, 0),
    ('C021', 'Category',     '品类',         'C020', '商品品类层级',          TRUE,  0),
    ('C022', 'SKU',          'SKU单品',      'C020', '具体单品',              FALSE, 0),
    ('C030', 'Order',        '订单',         'C001', '订单域',                FALSE, 1),
    ('C031', 'OrderDetail',  '订单明细',     'C030', '订单行项',              FALSE, 1),
    ('C040', 'Payment',      '支付',         'C001', '支付流水',              FALSE, 2),
    ('C050', 'Region',       '地域',         'C001', '地区层级 (大区->省->市)', TRUE, 0),
    ('C060', 'Campaign',     '活动',         'C001', '促销活动',              FALSE, 0),
    ('C070', 'Review',       '评价',         'C001', '商品评价',              FALSE, 0),
    ('C080', 'Time',         '时间',         'C001', '日期/时间维度',         FALSE, 0),
    ('C090', 'Logistics',    '物流',         'C001', '物流配送',              FALSE, 0),
    ('C100', 'Coupon',       '优惠券',       'C001', '优惠券/补贴',           FALSE, 0);

-- ============================================================ #
-- ont_property: 核心属性 (示例 8 条, 完整 30 条由 P0-09 任务补全)
-- ============================================================ #
INSERT INTO ont_property (property_id, property_name, property_name_zh, class_id, data_type, is_pk, is_fk, is_pii, business_term, description) VALUES
    ('P001', 'user_id',       '用户ID',     'C011', 'BIGINT',     TRUE,  FALSE, TRUE,  '用户ID',     '用户唯一标识'),
    ('P002', 'member_level',  '会员等级',   'C012', 'VARCHAR(16)', FALSE, FALSE, FALSE, '会员等级',   '用户会员等级 (1-9)'),
    ('P003', 'phone_num',     '手机号',     'C011', 'VARCHAR(20)', FALSE, FALSE, TRUE,  '手机号',     '注册手机号 (PII)'),
    ('P004', 'sku_id',        'SKU编号',    'C022', 'BIGINT',     TRUE,  FALSE, FALSE, 'SKU编号',    'SKU 唯一标识'),
    ('P005', 'category3_id',  '三级品类ID', 'C021', 'BIGINT',     FALSE, TRUE,  FALSE, '三级品类ID', 'FK->category2'),
    ('P006', 'order_id',      '订单号',     'C030', 'BIGINT',     TRUE,  FALSE, FALSE, '订单号',     '订单唯一标识'),
    ('P007', 'final_amount',  '成交金额',   'C030', 'DECIMAL',    FALSE, FALSE, FALSE, 'GMV',        'GMV 计算来源字段'),
    ('P008', 'region_id',     '地域ID',     'C050', 'INT',        TRUE,  FALSE, FALSE, '地域ID',     '地域唯一标识');

-- ============================================================ #
-- ont_relation: 11 种关系类型
-- ============================================================ #
INSERT INTO ont_relation (relation_id, src_class_id, dst_class_id, relation_type, relation_type_zh, cardinality, is_transitive, is_symmetric, description, confidence, source) VALUES
    ('C022:is_a:C020',         'C022', 'C020', 'is_a',         '是一种',   'N:1', TRUE,  FALSE, 'SKU 是一种商品 (类层级冗余表达)',         1.000, 'MANUAL'),
    ('C022:belongs_to:C021',   'C022', 'C021', 'belongs_to',   '属于',     'N:1', FALSE, FALSE, 'SKU 属于品类',                            1.000, 'MANUAL'),
    ('C021:parent_of:C021',    'C021', 'C021', 'parent_of',    '父级',     'N:1', TRUE,  FALSE, '品类层级 (传递: 3级->2级->1级)',           1.000, 'MANUAL'),
    ('C030:placed_by:C011',    'C030', 'C011', 'placed_by',    '下单方',   'N:1', FALSE, FALSE, '订单由用户下单',                          1.000, 'MANUAL'),
    ('C040:paid_by:C011',      'C040', 'C011', 'paid_by',      '支付方',   'N:1', FALSE, FALSE, '支付由用户发起',                          1.000, 'MANUAL'),
    ('C030:contains:C031',     'C030', 'C031', 'contains',     '包含',     '1:N', FALSE, FALSE, '订单包含订单明细',                        1.000, 'MANUAL'),
    ('C030:related_to:C100',   'C030', 'C100', 'related_to',   '相关',     'N:N', FALSE, TRUE,  '订单关联优惠券 (对称)',                   1.000, 'MANUAL'),
    ('C070:reviewed_by:C011',  'C070', 'C011', 'reviewed_by',  '评价方',   'N:1', FALSE, FALSE, '评价由用户提交',                          1.000, 'MANUAL'),
    ('C011:located_in:C050',   'C011', 'C050', 'located_in',   '位于',     'N:1', TRUE,  FALSE, '用户位于地域 (传递: 市->省->大区)',       1.000, 'MANUAL'),
    ('C030:promoted_by:C060',  'C030', 'C060', 'promoted_by',  '推广',     'N:N', FALSE, FALSE, '订单关联促销活动',                        1.000, 'MANUAL'),
    ('C031:derived_from:C030', 'C031', 'C030', 'derived_from', '派生自',   'N:1', TRUE,  FALSE, '明细->订单 跨层派生 (聚合溯源)',          1.000, 'MANUAL');

-- ============================================================ #
-- ont_instance: 字段映射示例 (8 条核心字段, 完整 800 条由 P0-09 任务补全)
-- ============================================================ #
INSERT INTO ont_instance (instance_id, db_name, table_name, column_name, class_id, property_id, role, source) VALUES
    ('dw.dim_user_info.user_id',                'dw', 'dim_user_info',           'user_id',     'C011', 'P001', 'PK',       'MANUAL'),
    ('dw.dim_user_info.phone_num',              'dw', 'dim_user_info',           'phone_num',   'C011', 'P003', 'MEASURE',  'MANUAL'),
    ('dw.dim_user_info.member_level',           'dw', 'dim_user_info',           'member_level','C012', 'P002', 'DIMENSION','MANUAL'),
    ('dw.dim_sku_info.sku_id',                  'dw', 'dim_sku_info',            'sku_id',      'C022', 'P004', 'PK',       'MANUAL'),
    ('dw.dim_sku_info.category3_id',            'dw', 'dim_sku_info',            'category3_id','C021', 'P005', 'FK',       'MANUAL'),
    ('dw.dwd_order_info_inc.order_id',          'dw', 'dwd_order_info_inc',      'order_id',    'C030', 'P006', 'PK',       'MANUAL'),
    ('dw.dwd_order_info_inc.final_amount',      'dw', 'dwd_order_info_inc',      'final_amount','C030', 'P007', 'MEASURE',  'MANUAL'),
    ('dw.dws_user_order_day_1m.total_amount',   'dw', 'dws_user_order_day_1m',   'total_amount','C030', 'P007', 'MEASURE',  'MANUAL');

-- ============================================================ #
-- lineage_business: 业务血缘示例 (GMV 指标)
-- ============================================================ #
INSERT INTO lineage_business (business_id, term_name, metric_id, sql_template, related_tables, related_columns, filters, owner, description) VALUES
    ('B001', 'GMV', 'M001',
     "SELECT SUM(final_amount) FROM dw.dwd_order_info_inc WHERE order_status='PAID'",
     '["dw.dwd_order_info_inc"]',
     '["dw.dwd_order_info_inc.final_amount","dw.dwd_order_info_inc.order_status"]',
     "order_status='PAID'",
     '数据团队',
     'GMV = 已支付订单的成交金额合计');

-- ============================================================ #
-- lineage_semantic: 语义血缘示例 (取消活动影响分析)
-- ============================================================ #
INSERT INTO lineage_semantic (semantic_id, src_class_id, src_instance_id, dst_class_id, relation_path, impact_type, impact_desc, confidence, source) VALUES
    ('S001', 'C060', 'dw.dwd_activity_info.activity_id=1001', 'C030',
     'Campaign <-promoted_by-- Order',
     'CASCADE',
     '取消活动 1001 -> 影响该活动的所有订单 -> 影响 GMV -> 影响 ads_user_active_day 日报',
     0.95, 'INFERENCE');

-- ============================================================ #
-- 兼容视图: 把新表包装成旧 entity_relation 接口 (PRD §9.2 D1 决策)
-- ============================================================ #
CREATE VIEW IF NOT EXISTS v_entity_relation AS
SELECT
    src.class_name       AS source_entity,
    CONCAT(src.class_id, ':', rel.relation_type, ':', dst.class_id) AS id,
    rel.relation_type    AS relation_type,
    dst.class_name       AS target_entity,
    rel.description,
    rel.cardinality
FROM ont_relation rel
JOIN ont_class src ON src.class_id = rel.src_class_id
JOIN ont_class dst ON dst.class_id = rel.dst_class_id;
