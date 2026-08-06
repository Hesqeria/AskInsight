"""Project readiness scoring system.

Mandatory checks before NL2SQL goes live:
  1. Lineage coverage — whether fact table FKs map to dimension tables
  2. Dimension relationship completeness — whether dim tables are reachable and REFERENCES are valid
  3. Field annotation coverage — whether columns have descriptions
  4. Alias coverage — whether dimension columns have Chinese aliases
  5. Business caliber coverage — whether measure columns have caliber descriptions
  6. Security whitelist coverage — whether tables are on the whitelist

A total score >= 70 is required to enable queries.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Optional

from app.core.log import logger


@dataclass
class DimensionScore:
    """Single-dimension score."""
    name: str
    score: float  # 0-100
    weight: float  # 0-1
    detail: dict = field(default_factory=dict)
    passed: bool = False
    threshold: float = 60.0  # Minimum threshold per dimension


@dataclass
class ReadinessReport:
    """Readiness report."""
    total_score: float
    passed: bool
    dimensions: list[DimensionScore]
    summary: str
    recommendations: list[str]


THRESHOLD_TOTAL = 70.0  # Total score threshold


async def check_readiness(meta_repo, table_infos: list[dict],
                          allowed_tables: set[str]) -> ReadinessReport:
    """Run readiness checks and return the report."""

    dims: list[DimensionScore] = []

    # ===== 1. Lineage coverage (25%) =====
    fact_tables = [t for t in table_infos if t.get("role") in ("fact", "measure")
                   or t["name"].startswith(("dwd_", "dws_", "ads_"))]
    fact_with_fk = []
    fact_without_fk = []
    for t in fact_tables:
        has_fk = any(c.get("role") == "foreign_key" for c in t.get("columns", []))
        if has_fk:
            fact_with_fk.append(t["name"])
        else:
            fact_without_fk.append(t["name"])

    lineage_score = (len(fact_with_fk) / len(fact_tables) * 100) if fact_tables else 0
    dims.append(DimensionScore(
        name="Lineage coverage",
        score=round(lineage_score, 1),
        weight=0.25,
        passed=lineage_score >= 60,
        threshold=60,
        detail={
            "fact_table_count": len(fact_tables),
            "with_fk_mapping": len(fact_with_fk),
            "without_fk_mapping": len(fact_without_fk),
            "missing_tables": fact_without_fk[:5],
        }
    ))

    # ===== 2. Dimension relationship completeness (25%) =====
    dim_tables = [t for t in table_infos if t["name"].startswith("dim_")]
    all_fk_cols = []
    for t in fact_tables:
        for c in t.get("columns", []):
            if c.get("role") == "foreign_key":
                all_fk_cols.append(f'{t["name"]}.{c["name"]}')

    # Check whether each FK can be matched to a dim table (inferred by naming)
    matched_fk = 0
    unmatched_fk = []
    for fk in all_fk_cols:
        tbl, col = fk.split(".")
        base = col.replace("_id", "")
        has_match = any(base in dt["name"] or dt["name"].endswith("_" + base)
                        for dt in dim_tables)
        if has_match:
            matched_fk += 1
        else:
            unmatched_fk.append(fk)

    dim_score = (matched_fk / len(all_fk_cols) * 100) if all_fk_cols else 0
    dims.append(DimensionScore(
        name="Dimension relationship completeness",
        score=round(dim_score, 1),
        weight=0.25,
        passed=dim_score >= 60,
        threshold=60,
        detail={
            "fk_column_count": len(all_fk_cols),
            "matched_dim": matched_fk,
            "unmatched": len(unmatched_fk),
            "missing_mappings": unmatched_fk[:5],
            "dim_table_count": len(dim_tables),
        }
    ))

    # ===== 3. Field annotation coverage (15%) =====
    all_cols = []
    cols_with_desc = []
    for t in table_infos:
        for c in t.get("columns", []):
            all_cols.append(f'{t["name"]}.{c["name"]}')
            if c.get("description", "").strip():
                cols_with_desc.append(f'{t["name"]}.{c["name"]}')

    annotation_score = (len(cols_with_desc) / len(all_cols) * 100) if all_cols else 0
    dims.append(DimensionScore(
        name="Field annotation coverage",
        score=round(annotation_score, 1),
        weight=0.15,
        passed=annotation_score >= 70,
        threshold=70,
        detail={
            "column_count": len(all_cols),
            "with_annotation": len(cols_with_desc),
            "without_annotation": len(all_cols) - len(cols_with_desc),
        }
    ))

    # ===== 4. Alias coverage (15%) =====
    dim_cols = []
    dim_cols_with_alias = []
    for t in dim_tables:
        for c in t.get("columns", []):
            if c.get("role") in ("dimension", "measure"):
                dim_cols.append(f'{t["name"]}.{c["name"]}')
                if c.get("alias"):
                    dim_cols_with_alias.append(f'{t["name"]}.{c["name"]}')

    alias_score = (len(dim_cols_with_alias) / len(dim_cols) * 100) if dim_cols else 0
    dims.append(DimensionScore(
        name="Alias coverage",
        score=round(alias_score, 1),
        weight=0.15,
        passed=alias_score >= 60,
        threshold=60,
        detail={
            "dim_column_count": len(dim_cols),
            "with_alias": len(dim_cols_with_alias),
            "without_alias": len(dim_cols) - len(dim_cols_with_alias),
        }
    ))

    # ===== 5. Business caliber coverage (10%) =====
    measure_cols = []
    measure_with_caliber = []
    for t in table_infos:
        for c in t.get("columns", []):
            if c.get("role") in ("measure", "metric"):
                measure_cols.append(f'{t["name"]}.{c["name"]}')
                desc = c.get("description", "")
                # Caliber markers: contains "caliber", specific formula, or enumerated values
                if any(kw in desc for kw in ["caliber", "(", ":", "include", "exclude", ":"]):
                    measure_with_caliber.append(f'{t["name"]}.{c["name"]}')

    caliber_score = (len(measure_with_caliber) / len(measure_cols) * 100) if measure_cols else 0
    dims.append(DimensionScore(
        name="Business caliber coverage",
        score=round(caliber_score, 1),
        weight=0.10,
        passed=caliber_score >= 50,
        threshold=50,
        detail={
            "measure_column_count": len(measure_cols),
            "with_caliber": len(measure_with_caliber),
            "without_caliber": len(measure_cols) - len(measure_with_caliber),
        }
    ))

    # ===== 6. Security whitelist coverage (10%) =====
    all_table_names = {t["name"] for t in table_infos}
    # Exclude metadata tables
    meta_tables = {"table_info", "column_info", "metric_info", "column_metric",
                   "column_value_info", "glossary", "feedback_log"}
    business_tables = all_table_names - meta_tables
    whitelisted = business_tables & allowed_tables

    whitelist_score = (len(whitelisted) / len(business_tables) * 100) if business_tables else 0
    dims.append(DimensionScore(
        name="Security whitelist coverage",
        score=round(whitelist_score, 1),
        weight=0.10,
        passed=whitelist_score >= 80,
        threshold=80,
        detail={
            "business_table_count": len(business_tables),
            "whitelisted": len(whitelisted),
            "not_whitelisted": len(business_tables - whitelisted),
            "missing_tables": list(business_tables - whitelisted)[:5],
        }
    ))

    # ===== Compute total score =====
    total = sum(d.score * d.weight for d in dims)
    all_dims_passed = all(d.passed for d in dims)
    overall_passed = total >= THRESHOLD_TOTAL and all_dims_passed

    # ===== Generate recommendations =====
    recommendations = []
    for d in dims:
        if not d.passed:
            recommendations.append(
                f"[WARN] {d.name} only {d.score} (threshold {d.threshold}), "
                f"needs to be raised above {d.threshold}"
            )
    if not recommendations:
        recommendations.append("[PASS] All dimensions meet the standard, project can be enabled")

    report = ReadinessReport(
        total_score=round(total, 1),
        passed=overall_passed,
        dimensions=dims,
        summary=f"Total {total:.1f}/100, {'[PASS] passed' if overall_passed else '[FAIL] not passed'} (threshold {THRESHOLD_TOTAL})",
        recommendations=recommendations,
    )

    logger.info(f"Readiness check: {report.summary}")
    for d in dims:
        status = "[PASS]" if d.passed else "[FAIL]"
        logger.info(f"  {status} {d.name}: {d.score} (weight {d.weight*100:.0f}%, threshold {d.threshold})")

    return report


def format_report_text(report: ReadinessReport) -> str:
    """Format the report as text."""
    lines = []
    lines.append("=" * 60)
    lines.append("  Project Readiness Score Report")
    lines.append("=" * 60)
    lines.append(f"\nTotal: {report.total_score}/100")
    lines.append(f"Status: {'[PASS] passed - queries can be enabled' if report.passed else '[FAIL] not passed - queries are locked'}")
    lines.append(f"Threshold: {THRESHOLD_TOTAL} points + all dimensions must meet the standard\n")
    lines.append("-" * 60)
    lines.append(f"{'Dimension':<32} {'Score':>6} {'Weight':>6} {'Threshold':>9} {'Status':>6}")
    lines.append("-" * 60)
    for d in report.dimensions:
        status = "PASS" if d.passed else "FAIL"
        lines.append(f"{d.name:<32} {d.score:>5.1f} {d.weight*100:>5.0f}% {d.threshold:>8.0f} {status:>6}")
    lines.append("-" * 60)
    lines.append("")
    lines.append("Recommendations:")
    for r in report.recommendations:
        lines.append(f"  {r}")
    lines.append("")
    lines.append("=" * 60)
    return chr(10).join(lines)
