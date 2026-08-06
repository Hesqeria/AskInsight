"""Drill-down attribution node: layer-by-layer drill-down from DWS -> DWD + lineage tracing + LLM attribution

Layer architecture:
  DWS (aggregation layer): dws_region_sales_daily, dws_category_sales_daily
  DWD (detail layer): fact_order + dim_* (star schema of the current data_agent database)
  ODS (raw layer): external data sources (not implemented)

Lineage relationships:
  DWS.total_amount ← SUM(DWD.fact_order.order_amount)
  DWS.total_quantity ← SUM(DWD.fact_order.order_quantity)
  DWS.order_count ← COUNT(DWD.fact_order.order_id)

Drill-down logic:
  When anomaly detection finds "total sales by region" anomaly:
  1. DWS layer drill-down: query dws_region_sales_daily -> which region/date is anomalous
  2. DWD layer drill-down: query fact_order -> which specific orders caused the anomaly
  3. Dimension attribution: split by region/category/time -> find the largest contributing factor
"""
from langgraph.runtime import Runtime
from sqlalchemy import text

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


# DWS->DWD lineage mapping (hardcoded; can be fetched dynamically from sql_lineage)
LINEAGE_MAP = {
    "dws_region_sales_daily": {
        "total_amount": {"source": "fact_order.order_amount", "transformation": "SUM"},
        "total_quantity": {"source": "fact_order.order_quantity", "transformation": "SUM"},
        "order_count": {"source": "fact_order.order_id", "transformation": "COUNT"},
    },
    "dws_category_sales_daily": {
        "total_amount": {"source": "fact_order.order_amount", "transformation": "SUM"},
        "total_quantity": {"source": "fact_order.order_quantity", "transformation": "SUM"},
        "order_count": {"source": "fact_order.order_id", "transformation": "COUNT"},
    },
}

# Drill-down dimension config
DRILL_DIMENSIONS = {
    "region": {
        "dws_table": "dws_region_sales_daily",
        "dws_group_col": "region_name",
        "dwd_join": "JOIN dim_region r ON fo.region_id = r.region_id",
        "dwd_group_col": "r.region_name",
    },
    "category": {
        "dws_table": "dws_category_sales_daily",
        "dws_group_col": "category",
        "dwd_join": "JOIN dim_product p ON fo.product_id = p.product_id",
        "dwd_group_col": "p.category",
    },
    "time": {
        "dws_table": "dws_region_sales_daily",
        "dws_group_col": "date_id",
        "dwd_join": "",
        "dwd_group_col": "fo.date_id",
    },
}


async def drill_down_analysis(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Drill-down attribution analysis: runs only when an anomaly is detected"""
    writer = runtime.stream_writer
    anomaly_status = state.get("anomaly_status", "normal")

    # Trigger drill-down only on anomaly
    if anomaly_status not in ("warning", "critical"):
        logger.info("Drill-down analysis: no anomaly, skipping")
        return {}

    writer({"stage": "Drill-down Attribution"})
    try:
        meta_repo = runtime.context["meta_doris_repository"]
        query = state.get("query", "")

        # 1. DWS layer drill-down: split by region
        dws_region = await _query_dws(meta_repo, "region")
        # 2. DWS layer drill-down: split by category
        dws_category = await _query_dws(meta_repo, "category")
        # 3. DWD layer drill-down: by region + date detail
        dwd_detail = await _query_dwd(meta_repo, "region")

        # 4. Attribution analysis: find the largest contributing anomaly factor
        attribution = _compute_attribution(dws_region, dws_category)

        result = {
            "dws_region": dws_region[:5],
            "dws_category": dws_category[:5],
            "dwd_detail": dwd_detail[:5],
            "attribution": attribution,
            "lineage": LINEAGE_MAP,
        }

        logger.info(f"Drill-down attribution: DWS region={len(dws_region)} rows, DWS category={len(dws_category)} rows, attribution={attribution.get('top_factor','')}")

        writer({"drill_down": result})
        return {"drill_down_result": result}
    except Exception as e:
        logger.error(f"Drill-down attribution error: {e}")
        return {}


async def _query_dws(repo, dimension: str) -> list:
    """Query DWS aggregation layer data"""
    config = DRILL_DIMENSIONS.get(dimension)
    if not config:
        return []

    table = config["dws_table"]
    group_col = config["dws_group_col"]
    sql = f"""
        SELECT {group_col} AS dimension_value,
               SUM(total_amount) AS total_amount,
               SUM(total_quantity) AS total_quantity,
               SUM(order_count) AS order_count
        FROM {table}
        GROUP BY {group_col}
        ORDER BY total_amount DESC
        LIMIT 10
    """
    try:
        result = await repo.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]
    except Exception as e:
        logger.warning(f"DWS query failed ({dimension}): {e}")
        return []


async def _query_dwd(repo, dimension: str) -> list:
    """Query DWD detail layer data"""
    config = DRILL_DIMENSIONS.get(dimension)
    if not config:
        return []

    join = config["dwd_join"]
    group_col = config["dwd_group_col"]
    sql = f"""
        SELECT {group_col} AS dimension_value,
               SUM(fo.order_amount) AS total_amount,
               SUM(fo.order_quantity) AS total_quantity,
               COUNT(*) AS order_count
        FROM fact_order fo
        {join}
        GROUP BY {group_col}
        ORDER BY total_amount DESC
        LIMIT 10
    """
    try:
        result = await repo.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]
    except Exception as e:
        logger.warning(f"DWD query failed ({dimension}): {e}")
        return []


def _compute_attribution(dws_region: list, dws_category: list) -> dict:
    """Attribution analysis: find the largest contributing anomaly factor

    Algorithm:
    1. Compute totals for each dimension
    2. Find the factor with max/min share
    3. Return top_factor + contribution%
    """
    attribution = {"top_factor": "", "contribution_pct": 0, "detail": []}

    if dws_region:
        total = sum(r.get("total_amount", 0) for r in dws_region)
        if total > 0:
            for r in dws_region:
                pct = r.get("total_amount", 0) / total * 100
                r["contribution_pct"] = round(pct, 2)
            top = max(dws_region, key=lambda x: x.get("total_amount", 0))
            attribution["top_factor"] = f"region={top.get('dimension_value', '?')}"
            attribution["contribution_pct"] = top.get("contribution_pct", 0)
            attribution["detail"].extend(dws_region[:3])

    if dws_category:
        total = sum(r.get("total_amount", 0) for r in dws_category)
        if total > 0:
            for r in dws_category:
                pct = r.get("total_amount", 0) / total * 100
                r["contribution_pct"] = round(pct, 2)
            top = max(dws_category, key=lambda x: x.get("total_amount", 0))
            attribution["top_factor"] += f" | category={top.get('dimension_value', '?')}"
            attribution["contribution_pct"] = max(attribution["contribution_pct"], top.get("contribution_pct", 0))
            attribution["detail"].extend(dws_category[:3])

    return attribution
