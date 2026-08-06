"""SQL dialect adapter for multi-database support.

Handles dialect-specific differences:
  - Quoting: backticks (Doris/MySQL) vs double-quotes (PG)
  - ROUND: ROUND(x,2) (Doris/MySQL) vs ROUND(x::numeric,2) (PG)  
  - LIMIT: LIMIT x (Doris/MySQL) vs LIMIT x (PG, same)
  - String concat: CONCAT (Doris/MySQL) vs || (PG)
  - Date functions: DATE_FORMAT (MySQL) vs TO_CHAR (PG)
  - Type casting: CAST(x AS type) (universal)

Used by generate_sql prompt to generate correct SQL for each dialect.
"""
from enum import Enum

class Dialect(str, Enum):
    DORIS = "doris"
    MYSQL = "mysql"
    PG = "postgresql"

# Dialect-specific SQL hints for the LLM prompt
DIALECT_HINTS = {
    Dialect.DORIS: """Database dialect rules for Apache Doris:
1. Use backticks for identifiers: `table_name`.`column_name`
2. ROUND(x, n) for rounding (Doris MySQL compatible)
3. DATE_FORMAT(dt, '%Y-%m-%d') for date formatting
4. CAST(x AS DECIMAL(18,2)) for type casting
5. LIMIT x for row limits (no OFFSET needed for simple queries)""",

    Dialect.MYSQL: """Database dialect rules for MySQL:
1. Use backticks for identifiers: `table_name`.`column_name`  
2. ROUND(x, n) for rounding
3. DATE_FORMAT(dt, '%Y-%m-%d') for date formatting
4. CONCAT(a, b) for string concatenation
5. LIMIT x for row limits""",

    Dialect.PG: """Database dialect rules for PostgreSQL:
1. Use double quotes for identifiers: "table_name"."column_name"
2. ROUND(x::numeric, n) for rounding (PG requires numeric cast)
3. TO_CHAR(dt, 'YYYY-MM-DD') for date formatting
4. a || b for string concatenation
5. LIMIT x for row limits
6. BOOLEAN fields: WHERE is_active = TRUE (not = 1)""",
}

# SQL template injection point: replaces {db_info} in generate_sql.prompt
def get_dialect_info(dialect: str) -> dict:
    """Get dialect information for the NL2SQL engine.
    
    Returns a dict with dialect name, version, and SQL rules.
    Used to populate the {db_info} placeholder in generate_sql.prompt.
    """
    try:
        d = Dialect(dialect.lower())
    except ValueError:
        d = Dialect.DORIS
    
    return {
        "version": f"AskInsight/{d.value}",
        "dialect": d.value,
        "hints": DIALECT_HINTS.get(d, DIALECT_HINTS[Dialect.DORIS]),
    }

def detect_dialect_from_config(db_name: str, host: str) -> Dialect:
    """Auto-detect dialect from database name and connection info.
    
    Heuristic:
    - If connected via Doris port (9030) → Doris
    - If db_name is "dw" or starts with "doris" → Doris
    - Default → MySQL (Doris uses MySQL protocol)
    """
    if "doris" in db_name.lower() or "doris" in host.lower():
        return Dialect.DORIS
    if db_name == "dw":
        return Dialect.DORIS
    return Dialect.MYSQL
