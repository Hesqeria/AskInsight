"""SQL safety gateway node: covers B-A6/S2/S3/S5
Before validate_sql (syntax check), first run a safety check:
1. Forbid DDL/DML (DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE)
2. Only allow whitelisted tables
3. Force LIMIT
4. Detect full-table-scan risk
"""
import re
from langgraph.runtime import Runtime
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

# Dangerous keywords (SQL injection protection)
DANGEROUS_PATTERNS = [
    r"\b(drop|delete|truncate|alter|create|insert|update|grant|revoke|execute|exec|shutdown|merge)\b",
    r"--",  # SQL comment
    r"/\*", r"\*/",
    r"\binto\s+outfile\b",
    r"\bload_file\b",
    r"\bxp_cmdshell\b",
]

# Whitelisted tables (loaded from meta_config.yaml, with hardcoded fallback)
ALLOWED_TABLES = {
    # Enterprise DW DIM
    "dim_user_info", "dim_sku_info", "dim_base_province", "dim_base_region",
    "dim_date", "dim_coupon_info", "dim_activity_info", "dim_activity_rule",
    "dim_base_category1", "dim_base_category2", "dim_base_category3",
    "dim_base_dic", "dim_base_sale_attr", "dim_base_trademark",
    "dim_spu_info", "dim_promotion_pos",
    # Enterprise DW DWD
    "dwd_order_info_inc", "dwd_order_detail_inc", "dwd_payment_info_inc",
    "dwd_cart_info_inc", "dwd_comment_info_inc", "dwd_favor_info_inc",
    "dwd_coupon_use_inc", "dwd_order_refund_info_inc",
    "dwd_start_log_inc", "dwd_page_log_inc", "dwd_action_log_inc",
    "dwd_display_log_inc", "dwd_error_log_inc",
    # Enterprise DW DWS
    "dws_user_user_order_day", "dws_user_user_cart_day", "dws_user_user_payment_day",
    "dws_sku_sku_order_day", "dws_sku_sku_payment_day", "dws_sku_sku_cart_day",
    "dws_region_region_order_day", "dws_activity_activity_order_day",
    "dws_coupon_coupon_use_day",
    "dws_sales_wide",
    # Enterprise DW ADS
    "ads_gmv_total_day", "ads_region_gmv_rank", "ads_sku_topn",
    "ads_user_conversion", "ads_user_repurchase", "ads_user_retention_day",
    "ads_activity_conversion", "ads_coupon_roi",
    # Metadata tables
    "table_info", "column_info", "metric_info", "column_metric",
    "column_value_info", "glossary", "feedback_log",
    # CTE aliases
    "t", "sub", "tmp", "cte", "ranked", "filtered",
}


async def validate_sql_safety(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "SQL Safety Check"})
    try:
        sql = state.get("sql", "")
        sql_upper = sql.upper().strip()

        # 1. Forbid DDL/DML
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, sql_upper, re.IGNORECASE):
                msg = f"SQL contains dangerous operation: {pattern}"
                logger.warning(msg)
                return {"error": msg, "sql": sql}

        # 2. Must be SELECT
        if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
            return {"error": "Only SELECT queries are allowed", "sql": sql}

        # 3. Whitelisted table check
        found_tables = set(re.findall(r'\b(from|join)\s+(\w+)', sql, re.IGNORECASE))
        for _, table_name in found_tables:
            if table_name.lower() not in ALLOWED_TABLES:
                msg = f"Table '{table_name}' is not in the whitelist"
                logger.warning(msg)
                return {"error": msg, "sql": sql}

        # 4. Force LIMIT (add it if missing)
        if "LIMIT" not in sql_upper:
            sql = sql.rstrip(";") + " LIMIT 1000;"
            logger.info("SQL has no LIMIT, automatically added LIMIT 1000")

        # 5. Full-table-scan detection (single-table query without WHERE)
        if not re.search(r'\bwhere\b', sql, re.IGNORECASE):
            # Only warn for single-table queries (multi-table JOIN can omit WHERE)
            table_count = len(re.findall(r'\b(from|join)\s+\w+', sql, re.IGNORECASE))
            if table_count == 1:
                logger.warning("Detected full-table scan (single table without WHERE)")

        logger.info("SQL safety check passed")
        return {"sql": sql, "error": None}
    except Exception as e:
        logger.error(f"SQL safety check error: {e}")
        return {"error": f"Safety check error: {e}", "sql": state.get("sql", "")}
