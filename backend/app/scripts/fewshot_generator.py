"""Auto-generate few-shot SQL examples from database schema.

For cold-start deployments with no query history, this generates
domain-specific SQL examples that help the LLM understand the schema.

Generated examples include:
  - "Top 5 X by Y" for each fact table
  - "X grouped by Y" for each dimension
  - "X filtered by Y" for date/status columns
"""
import yaml
from pathlib import Path
from app.core.log import logger


def generate_fewshot_from_config(config_path: str = None, table_infos: list = None) -> list[dict]:
    """Generate few-shot SQL examples from meta_config.

    Returns list of {question: str, sql: str} pairs.
    """
    if table_infos is None and config_path:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        table_infos = config.get("tables", [])

    if not table_infos:
        return []

    examples = []
    
    # Classify tables
    fact_tables = [t for t in table_infos if t["role"] in ("fact", "measure")]
    dim_tables = [t for t in table_infos if t["name"].startswith("dim_")]
    
    for ft in fact_tables:
        tn = ft["name"]
        measures = [c for c in ft["columns"] if c["role"] == "measure"]
        dimensions = [c for c in ft["columns"] if c["role"] == "dimension"]
        fks = [c for c in ft["columns"] if c["role"] == "foreign_key"]
        
        if not measures:
            continue

        # 1. Total aggregation
        m = measures[0]
        m_alias = m["alias"][0] if m["alias"] else m["name"].replace("_", " ")
        examples.append({
            "question": f"Total {m_alias}",
            "sql": f"SELECT SUM({m['name']}) AS total_{m['name']} FROM {tn} LIMIT 1000"
        })

        # 2. Top 5 by measure
        if dimensions:
            d = dimensions[0]
            d_alias = d["alias"][0] if d["alias"] else d["name"].replace("_", " ")
            examples.append({
                "question": f"Top 5 {d_alias} by {m_alias}",
                "sql": f"SELECT {d['name']}, SUM({m['name']}) AS total_{m['name']} FROM {tn} GROUP BY {d['name']} ORDER BY total_{m['name']} DESC LIMIT 5"
            })

        # 3. FK ↔ dim join
        if fks and dim_tables:
            fk = fks[0]
            fk_base = fk["name"].replace("_id", "")
            matching_dim = next((dt for dt in dim_tables if fk_base in dt["name"]), None)
            if matching_dim:
                dim_cols = [c for c in matching_dim["columns"] if c["role"] == "dimension"]
                if dim_cols:
                    dc = dim_cols[0]
                    dc_alias = dc["alias"][0] if dc["alias"] else dc["name"].replace("_", " ")
                    examples.append({
                        "question": f"{dc_alias} {m_alias} ranking",
                        "sql": f"SELECT d.{dc['name']}, SUM(f.{m['name']}) AS total_{m['name']} FROM {tn} f JOIN {matching_dim['name']} d ON f.{fk['name']} = d.id GROUP BY d.{dc['name']} ORDER BY total_{m['name']} DESC LIMIT 1000"
                    })

    logger.info(f"Auto-generated {len(examples)} few-shot examples from schema")
    return examples


def format_fewshot_prompt(examples: list[dict]) -> str:
    """Format examples as prompt text for injection into generate_sql.prompt."""
    if not examples:
        return ""
    
    lines = ["[Auto-generated Schema Examples]"]
    for i, ex in enumerate(examples[:10], 1):  # 最多 10 条
        lines.append(f"Q: {ex['question']}")
        lines.append(f"SQL: {ex['sql']}")
        lines.append("")
    return "\n".join(lines)
