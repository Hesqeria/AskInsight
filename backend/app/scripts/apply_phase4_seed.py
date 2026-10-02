"""Seed the lineage_business / lineage_semantic / rl_policy_stats tables.

Ontology seed (ont_class/property/relation/instance) is skipped because
the production Doris already has richer data (16/30/14/74 rows vs our
seed's 16/8/11/8).

Run from backend/:
    python -m app.scripts.apply_phase4_seed
"""
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.conf.app_config import app_config
from app.core.log import logger
from app.rl import ACTIVE_POLICIES, DEFAULT_ALPHA, DEFAULT_BETA


SEED_BUSINESS = [
    # (business_id, term_name, metric_id, sql_template, related_tables, related_columns, filters, owner, description)
    (
        "B001", "GMV", "M001",
        "SELECT SUM(final_amount) FROM dw.dwd_order_info_inc WHERE order_status='PAID'",
        '["dw.dwd_order_info_inc"]',
        '["dw.dwd_order_info_inc.final_amount","dw.dwd_order_info_inc.order_status"]',
        "order_status='PAID'",
        "data-team",
        "GMV = 已支付订单的成交金额合计",
    ),
    (
        "B002", "DAU", "M002",
        "SELECT COUNT(DISTINCT user_id) FROM dw.dws_user_active_day WHERE dt = CURRENT_DATE()",
        '["dw.dws_user_active_day"]',
        '["dw.dws_user_active_day.user_id","dw.dws_user_active_day.dt"]',
        "dt = CURRENT_DATE()",
        "data-team",
        "DAU = 当日去重活跃用户数",
    ),
    (
        "B003", "order_count", "M003",
        "SELECT COUNT(*) FROM dw.dwd_order_info_inc",
        '["dw.dwd_order_info_inc"]',
        '["dw.dwd_order_info_inc.order_id"]',
        "",
        "data-team",
        "订单总数(不限状态)",
    ),
]

SEED_SEMANTIC = [
    # (semantic_id, src_class, src_instance, dst_class, relation_path, impact_type, impact_desc, confidence)
    (
        "S001", "C060", "dw.dwd_activity_info.activity_id=1001", "C030",
        "Campaign <-promoted_by-- Order",
        "CASCADE",
        "取消活动 1001 -> 影响该活动的所有订单 -> 影响 GMV -> 影响 ads_user_active_day 日报",
        0.95,
    ),
    (
        "S002", "C021", None, "C022",
        "Category <-belongs_to-- SKU",
        "AGGREGATE",
        "下架某品类 -> 影响该品类下所有 SKU -> 影响 SKU 数指标",
        1.0,
    ),
]


async def apply_seed():
    cfg = app_config.doris
    url = f"mysql+asyncmy://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/data_agent"
    engine = create_async_engine(url, echo=False)

    biz_n = sem_n = rl_n = 0
    async with engine.begin() as conn:
        # ---- lineage_business ----
        existing_biz = await conn.execute(text("SELECT COUNT(*) FROM lineage_business"))
        if existing_biz.scalar() == 0:
            for row in SEED_BUSINESS:
                try:
                    await conn.execute(text("""
                        INSERT INTO lineage_business
                            (business_id, term_name, metric_id, sql_template,
                             related_tables, related_columns, filters,
                             owner, description, updated_at)
                        VALUES (:bid, :term, :mid, :sql, :rt, :rc, :f, :o, :d, NOW())
                    """), {
                        "bid": row[0], "term": row[1], "mid": row[2],
                        "sql": row[3], "rt": row[4], "rc": row[5], "f": row[6],
                        "o": row[7], "d": row[8],
                    })
                    biz_n += 1
                except Exception as e:
                    logger.warning(f"lineage_business insert failed for {row[0]}: {e}")
        else:
            logger.info(f"lineage_business already has {existing_biz.scalar()} rows, skipping")

        # ---- lineage_semantic ----
        existing_sem = await conn.execute(text("SELECT COUNT(*) FROM lineage_semantic"))
        if existing_sem.scalar() == 0:
            for row in SEED_SEMANTIC:
                try:
                    await conn.execute(text("""
                        INSERT INTO lineage_semantic
                            (semantic_id, src_class_id, src_instance_id,
                             dst_class_id, relation_path, impact_type,
                             impact_desc, confidence, source, created_at)
                        VALUES (:sid, :sc, :si, :dc, :rp, :it, :id, :conf, 'INFERENCE', NOW())
                    """), {
                        "sid": row[0], "sc": row[1], "si": row[2], "dc": row[3],
                        "rp": row[4], "it": row[5], "id": row[6], "conf": row[7],
                    })
                    sem_n += 1
                except Exception as e:
                    logger.warning(f"lineage_semantic insert failed for {row[0]}: {e}")
        else:
            logger.info(f"lineage_semantic already has {existing_sem.scalar()} rows, skipping")

        # ---- rl_policy_stats (Beta(1,1) priors for all active policies) ----
        existing_rl = await conn.execute(text("SELECT COUNT(*) FROM rl_policy_stats"))
        if existing_rl.scalar() == 0:
            for policy in ACTIVE_POLICIES:
                try:
                    await conn.execute(text("""
                        INSERT INTO rl_policy_stats
                            (policy_name, alpha, beta, total_calls,
                             positive_rewards, negative_rewards, last_updated)
                        VALUES (:n, :a, :b, 0, 0, 0, NOW())
                    """), {
                        "n": policy,
                        "a": DEFAULT_ALPHA, "b": DEFAULT_BETA,
                    })
                    rl_n += 1
                except Exception as e:
                    logger.warning(f"rl_policy_stats insert failed for {policy}: {e}")
        else:
            logger.info(f"rl_policy_stats already has {existing_rl.scalar()} rows, skipping")

    await engine.dispose()

    print()
    print(f"=== Phase 4 seed summary ===")
    print(f"  lineage_business inserted: {biz_n}")
    print(f"  lineage_semantic inserted: {sem_n}")
    print(f"  rl_policy_stats inserted:  {rl_n}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(apply_seed()))
