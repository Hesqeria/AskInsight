"""Auto-bootstrap meta_config from database schema.

Scans the actual database, auto-detects table roles, FK columns, 
generates Chinese aliases, and produces a ready-to-use meta_config.

Usage:
    python -m app.scripts.auto_bootstrap --db dw --output conf/meta_config.yaml
    python -m app.scripts.auto_bootstrap --db data_agent --output conf/meta_config_mysql.yaml
"""
import argparse
import yaml
import re
from pathlib import Path
from app.core.log import logger

# Column name → Chinese alias mapping (extensible)
_ALIAS_MAP = {
    # Common ID columns
    "id": [],
    "user_id": ["user", "customer"],
    "sku_id": ["product", "SKU", "item"],
    "order_id": ["order"],
    "product_id": ["product", "item"],
    "province_id": ["province"],
    "region_id": ["region", "area"],
    "customer_id": ["customer", "client"],
    "activity_id": ["activity"],
    "coupon_id": ["coupon"],
    "spu_id": ["SPU"],
    "category1_id": ["category", "level1 category"],
    "category2_id": ["category", "level2 category"],
    "category3_id": ["category", "level3 category"],
    "tm_id": ["brand", "trademark"],
    "date_id": ["date"],
    
    # Dimension columns
    "name": ["name"],
    "sku_name": ["product name"],
    "spu_name": ["SPU name"],
    "tm_name": ["brand", "trademark"],
    "category1_name": ["category", "level1"],
    "category2_name": ["category", "level2"],
    "category3_name": ["category", "level3"],
    "region_name": ["region", "area"],
    "province_name": ["province"],
    "customer_name": ["customer name"],
    "user_name": ["user name"],
    "login_name": ["login name"],
    "nick_name": ["nickname"],
    "phone_num": ["phone"],
    "gender": ["gender"],
    "birthday": ["birthday"],
    "user_level": ["member level", "tier"],
    "member_level": ["member level"],
    "order_status": ["order status"],
    "payment_type": ["payment type"],
    "payment_way": ["payment way"],
    "activity_name": ["activity name"],
    "coupon_name": ["coupon name"],
    "coupon_type": ["coupon type"],
    "pos_location": ["position", "location"],
    "rfm_segment": ["RFM segment"],
    "lifecycle_stage": ["lifecycle stage"],
    "preferred_category": ["preferred category"],
    "preferred_brand": ["preferred brand"],
    
    # Measure columns
    "total_amount": ["total amount", "sales", "GMV"],
    "order_amount": ["sales amount", "GMV"],
    "payment_amount": ["payment amount"],
    "original_total_amount": ["original amount"],
    "activity_reduce_amount": ["activity discount"],
    "coupon_reduce_amount": ["coupon discount"],
    "benefit_amount": ["benefit amount", "discount"],
    "order_price": ["order price", "unit price"],
    "cart_price": ["cart price"],
    "price": ["price"],
    "order_count": ["order count"],
    "order_sku_num": ["sales quantity"],
    "order_sku_count": ["SKU count"],
    "sku_num": ["quantity"],
    "total_quantity": ["total quantity", "sales volume"],
    "split_total_amount": ["split amount"],
    "feight_fee": ["freight fee"],
    "gmv": ["GMV", "sales"],
    "repurchase_rate": ["repurchase rate"],
    "conversion_rate": ["conversion rate"],
    "roi": ["ROI"],
    
    # Other
    "dt": ["date", "time"],
    "create_time": ["create time"],
    "operate_time": ["update time"],
    "ts": ["timestamp"],
}


async def auto_bootstrap(session_factory, db_name: str, output_path: str = None):
    """Auto-generate meta_config from database schema.
    
    Args:
        session_factory: SQLAlchemy async_sessionmaker
        db_name: database name
        output_path: optional output YAML path
    """
    from sqlalchemy import text
    
    async with session_factory() as session:
        # 1. Get all tables
        result = await session.execute(
            text("SHOW TABLES FROM `{db_name}`".format(db_name=db_name))
            if db_name else text("SHOW TABLES")
        )
        all_tables = [row[0] for row in result.fetchall()]
        
        # Skip system tables
        skip_patterns = ["table_info", "column_info", "metric_info", "column_metric",
                         "column_value_info", "feedback_log", "glossary", "audit_log",
                         "anomaly_event", "sql_lineage", "metric_baseline",
                         "__internal_schema", "information_schema", "mysql"]
        tables = [t for t in all_tables if t not in skip_patterns]
        logger.info(f"Found {len(tables)} business tables (skipped {len(all_tables)-len(tables)} system tables)")
        
        config_tables = []
        
        for tname in tables:
            # 2. Get column info
            try:
                result = await session.execute(text(f"DESCRIBE `{tname}`"))
                cols_raw = result.fetchall()
            except Exception as e:
                logger.warning(f"Skip {tname}: {e}")
                continue
            
            # 3. Determine table role
            if tname.startswith("dim_"):
                role = "dimension"
            elif tname.startswith(("dwd_", "fact_")):
                role = "fact"
            elif tname.startswith(("dws_", "ads_")):
                role = "measure"  # summary tables
            else:
                role = "dimension"  # default
            
            # 4. Process columns
            columns = []
            for col in cols_raw:
                cname, ctype = col[0], col[1]
                
                # Determine column role
                if cname == "id" or cname == tname + "_id":
                    col_role = "primary_key"
                elif cname.endswith("_id") and cname != "id":
                    col_role = "foreign_key"
                elif ctype and any(t in ctype.upper() for t in ["INT", "DECIMAL", "FLOAT", "DOUBLE", "BIGINT"]):
                    col_role = "measure"
                else:
                    col_role = "dimension"
                
                # Generate aliases
                aliases = _ALIAS_MAP.get(cname, [])
                if not aliases and col_role == "dimension":
                    aliases = [cname.replace("_", " ")]
                
                # Generate description
                if cname in _ALIAS_MAP:
                    desc = f"{cname} - {' / '.join(_ALIAS_MAP[cname][:2])}"
                else:
                    desc = cname.replace("_", " ").title()
                
                columns.append({
                    "name": cname,
                    "type": ctype or "VARCHAR",
                    "role": col_role,
                    "description": desc,
                    "alias": aliases,
                })
            
            # 5. Generate table description
            desc = tname.replace("_", " ").title()
            if role == "dimension":
                desc = f"Dimension: {desc}"
            elif role == "fact":
                desc = f"Fact: {desc}"
            
            config_tables.append({
                "name": tname,
                "role": role,
                "description": desc,
                "columns": columns,
            })
            
            logger.info(f"  {tname}: {len(columns)} cols, role={role}")

    config = {"tables": config_tables}
    
    # Output
    if output_path:
        output_path = Path(output_path)
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        logger.info(f"Generated meta_config: {output_path} ({len(config_tables)} tables)")
    
    return config


def cli_entry():
    """CLI entry point (sync wrapper)"""
    import asyncio
    
    parser = argparse.ArgumentParser(description="Auto-bootstrap meta_config from database")
    parser.add_argument("--db", required=True, help="Database name")
    parser.add_argument("--output", default="conf/meta_config_auto.yaml", help="Output YAML path")
    args = parser.parse_args()
    
    async def run():
        from app.clients.doris_client_manager import doris_client_manager
        doris_client_manager.init()
        return await auto_bootstrap(doris_client_manager.session_factory, args.db, args.output)
    
    asyncio.run(run())


if __name__ == "__main__":
    cli_entry()
