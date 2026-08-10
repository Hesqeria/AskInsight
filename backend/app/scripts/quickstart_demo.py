"""AskInsight quick-start demo: create warehouse tables, seed data, build knowledge, run a query.

Usage (from backend/):
    python -m app.scripts.quickstart_demo --steps all
    python -m app.scripts.quickstart_demo --steps tables
    python -m app.scripts.quickstart_demo --steps knowledge
    python -m app.scripts.quickstart_demo --steps query --question "各地区的销售额是多少"
"""
import argparse
import asyncio
import random
from pathlib import Path

from app.core.log import logger

_DDL = [
    """CREATE TABLE IF NOT EXISTS dim_region (
        region_id   BIGINT COMMENT 'Region ID',
        province    VARCHAR(64) COMMENT 'Province',
        region_name VARCHAR(64) COMMENT 'Macro region',
        country     VARCHAR(32) COMMENT 'Country'
    ) DUPLICATE KEY(region_id)
      DISTRIBUTED BY HASH(region_id) BUCKETS 3
      PROPERTIES ("replication_num" = "1")""",
    """CREATE TABLE IF NOT EXISTS dim_date (
        date_id INT COMMENT 'yyyyMMdd',
        year    INT COMMENT 'Year',
        quarter INT COMMENT 'Quarter',
        month   INT COMMENT 'Month',
        day     INT COMMENT 'Day'
    ) UNIQUE KEY(date_id)
      DISTRIBUTED BY HASH(date_id) BUCKETS 3
      PROPERTIES ("replication_num" = "1")""",
    """CREATE TABLE IF NOT EXISTS dim_product (
        product_id   BIGINT COMMENT 'Product ID',
        product_name VARCHAR(128) COMMENT 'Product name',
        category     VARCHAR(64) COMMENT 'Category',
        brand        VARCHAR(64) COMMENT 'Brand'
    ) DUPLICATE KEY(product_id)
      DISTRIBUTED BY HASH(product_id) BUCKETS 3
      PROPERTIES ("replication_num" = "1")""",
    """CREATE TABLE IF NOT EXISTS dim_customer (
        customer_id   BIGINT COMMENT 'Customer ID',
        customer_name VARCHAR(64) COMMENT 'Customer name',
        gender        VARCHAR(8)  COMMENT 'Gender',
        member_level  VARCHAR(16) COMMENT 'Member level'
    ) DUPLICATE KEY(customer_id)
      DISTRIBUTED BY HASH(customer_id) BUCKETS 3
      PROPERTIES ("replication_num" = "1")""",
    """CREATE TABLE IF NOT EXISTS fact_order (
        order_id       BIGINT COMMENT 'Order ID',
        customer_id    BIGINT COMMENT 'FK -> dim_customer',
        product_id     BIGINT COMMENT 'FK -> dim_product',
        date_id        INT    COMMENT 'FK -> dim_date',
        region_id      BIGINT COMMENT 'FK -> dim_region',
        order_quantity INT             COMMENT 'Quantity',
        order_amount   DECIMAL(18,2)   COMMENT 'Amount'
    ) DUPLICATE KEY(order_id)
      DISTRIBUTED BY HASH(order_id) BUCKETS 10
      PROPERTIES ("replication_num" = "1")""",
    """CREATE TABLE IF NOT EXISTS dws_sales_wide (
        date_id        INT,
        region_name    VARCHAR(64),
        customer_name  VARCHAR(64),
        category       VARCHAR(64),
        brand          VARCHAR(64),
        total_amount   DECIMAL(20,2),
        total_quantity BIGINT,
        order_count    BIGINT
    ) DUPLICATE KEY(date_id, region_name)
      DISTRIBUTED BY HASH(date_id) BUCKETS 5
      PROPERTIES ("replication_num" = "1")""",
    """CREATE TABLE IF NOT EXISTS ads_customer_profile (
        customer_id        BIGINT,
        customer_name      VARCHAR(64),
        member_level       VARCHAR(16),
        total_orders       BIGINT,
        total_amount       DECIMAL(20,2),
        avg_order_amount   DECIMAL(20,2),
        preferred_category VARCHAR(64),
        rfm_segment        VARCHAR(32),
        is_high_value      BOOLEAN,
        is_churn_risk      BOOLEAN,
        lifecycle_stage    VARCHAR(32)
    ) UNIQUE KEY(customer_id)
      DISTRIBUTED BY HASH(customer_id) BUCKETS 5
      PROPERTIES ("replication_num" = "1")""",
]


def _rows_to_sql(table, columns, rows):
    cols = ", ".join(columns)
    vals = []
    for r in rows:
        esc = []
        for v in r:
            if v is None:
                esc.append("NULL")
            elif isinstance(v, (int, float)):
                esc.append(str(v))
            else:
                esc.append("'" + str(v).replace("'", "''") + "'")
        vals.append("(" + ", ".join(esc) + ")")
    return "INSERT INTO " + table + " (" + cols + ") VALUES " + ", ".join(vals) + ";"

def _seed_sql():
    statements = [
        _rows_to_sql("dim_region", ["region_id", "province", "region_name", "country"],
                     [[1, "北京", "华北", "中国"], [2, "上海", "华东", "中国"],
                      [3, "广东", "华南", "中国"], [4, "四川", "西南", "中国"]]),
        _rows_to_sql("dim_product", ["product_id", "product_name", "category", "brand"],
                     [[101, "智能手机A", "手机", "品牌X"], [102, "智能手机B", "手机", "品牌Y"],
                      [201, "牛奶", "食品", "品牌Z"], [202, "咖啡", "食品", "品牌Z"],
                      [301, "笔记本电脑", "数码", "品牌X"]]),
        _rows_to_sql("dim_customer", ["customer_id", "customer_name", "gender", "member_level"],
                     [[1001, "张伟", "男", "VIP"], [1002, "李娜", "女", "普通"],
                      [1003, "王芳", "女", "VIP"], [1004, "刘强", "男", "普通"]]),
        _rows_to_sql("dim_date", ["date_id", "year", "quarter", "month", "day"],
                     [[20260801, 2026, 3, 8, 1], [20260802, 2026, 3, 8, 2],
                      [20260803, 2026, 3, 8, 3], [20260809, 2026, 3, 8, 9]]),
    ]
    random.seed(42)
    facts = []
    for i in range(1, 21):
        cid = random.choice([1001, 1002, 1003, 1004])
        pid = random.choice([101, 102, 201, 202, 301])
        did = random.choice([20260801, 20260802, 20260803, 20260809])
        rid = random.choice([1, 2, 3, 4])
        qty = random.randint(1, 5)
        amount = round(qty * random.choice([1999, 2999, 12, 35, 5999]), 2)
        facts.append([i, cid, pid, did, rid, qty, amount])
    statements.append(_rows_to_sql(
        "fact_order", ["order_id", "customer_id", "product_id", "date_id",
                       "region_id", "order_quantity", "order_amount"], facts))

    reg = {1: "华北", 2: "华东", 3: "华南", 4: "西南"}
    cust = {1001: "张伟", 1002: "李娜", 1003: "王芳", 1004: "刘强"}
    prod = {101: ("手机", "品牌X"), 102: ("手机", "品牌Y"), 201: ("食品", "品牌Z"),
            202: ("食品", "品牌Z"), 301: ("数码", "品牌X")}
    agg = {}
    for o in facts:
        key = (o[3], o[4], o[1], o[2])
        agg.setdefault(key, [0, 0, 0])
        agg[key][0] += o[6]
        agg[key][1] += o[5]
        agg[key][2] += 1
    wide = []
    for (did, rid, cid, pid), (amt, qty, cnt) in agg.items():
        cat, brand = prod[pid]
        wide.append([did, reg[rid], cust[cid], cat, brand, round(amt, 2), qty, cnt])
    statements.append(_rows_to_sql(
        "dws_sales_wide", ["date_id", "region_name", "customer_name", "category",
                           "brand", "total_amount", "total_quantity", "order_count"],
        wide))
    return statements


async def step_tables_seed():
    from app.clients.doris_client_manager import doris_client_manager
    from sqlalchemy import text
    doris_client_manager.init()
    async with doris_client_manager.session_factory() as session:
        for ddl in _DDL:
            await session.execute(text(ddl))
        logger.info("Tables created: %d", len(_DDL))
        for stmt in _seed_sql():
            await session.execute(text(stmt))
        await session.commit()
        for t in ["dim_region", "dim_product", "dim_customer", "dim_date",
                  "fact_order", "dws_sales_wide", "ads_customer_profile"]:
            r = await session.execute(text("SELECT COUNT(1) FROM " + t))
            logger.info("  %s rows=%s", t, r.scalar())


async def step_build_knowledge():
    from app.scripts.build_meta_knowledge import build
    cfg = Path(__file__).parents[2] / "conf" / "meta_config.yaml"
    if cfg.exists():
        await build(cfg)
        logger.info("Knowledge base built from %s", cfg)
    else:
        logger.warning("meta_config.yaml not found; skipping (run auto_bootstrap first)")


async def step_query(question):
    from app.api.dependencies import get_query_service
    qs = await get_query_service()
    logger.info("Question: %s", question)
    async for line in qs.query(question, history=[], username="demo"):
        logger.info("SSE %s", line[:200])


async def _run(steps, question):
    for s in steps:
        if s in ("all", "tables", "seed"):
            await step_tables_seed()
        elif s == "knowledge":
            await step_build_knowledge()
        elif s == "query":
            await step_query(question)


def main(argv=None):
    parser = argparse.ArgumentParser(description="AskInsight quick-start demo")
    parser.add_argument("--steps", default="all",
                        help="comma separated: tables,seed,knowledge,query | all")
    parser.add_argument("--question", default="各地区销售额是多少")
    args = parser.parse_args(argv)
    steps = ["all"] if args.steps == "all" else [s.strip() for s in args.steps.split(",")]
    asyncio.run(_run(steps, args.question))


if __name__ == "__main__":
    main()
