"""Data modeling advisor: business context -> strategy recommendations."""

from dataclasses import dataclass, field


@dataclass
class ModelingOption:
    name: str
    description: str
    pros: list = field(default_factory=list)
    cons: list = field(default_factory=list)
    recommendation: str = ""


def _has_keyword(text: str, keywords: list) -> bool:
    return any(kw in text for kw in keywords)


def advise_modeling(business_desc: str, table_structures: dict = None) -> dict:
    """Provide modeling recommendations based on business context."""
    desc_lower = (business_desc or "").lower()
    options = []

    if _has_keyword(desc_lower, ["history", "status change", "trace", "trajectory"]):
        options.append(ModelingOption(
            name="Zip Table (SCD Type 2)",
            description="Records effective time range per row, preserving full change history",
            pros=["Full history traceability", "Supports point-in-time queries"],
            cons=["Higher storage cost", "Query requires time range filter"],
            recommendation="Use when full history traceability is required",
        ))
        options.append(ModelingOption(
            name="Daily Snapshot",
            description="Daily full/incremental snapshot of current state",
            pros=["Simple queries on latest partition", "Controlled storage cost"],
            cons=["No arbitrary point-in-time query", "Only last state of each day"],
            recommendation="Use when only current state matters",
        ))

    if _has_keyword(desc_lower, ["aggregate", "summary", "metric", "report", "kpi", "dashboard"]):
        options.append(ModelingOption(
            name="DWS Light Summary Layer",
            description="Pre-aggregate metrics by business dimensions (day/week/month x product/region)",
            pros=["Excellent query performance", "Simple report development"],
            cons=["Reduced flexibility (fixed granularity)", "Storage redundancy"],
            recommendation="Recommended for regular reports and dashboards",
        ))

    if _has_keyword(desc_lower, ["real-time", "stream", "sub-second"]):
        options.append(ModelingOption(
            name="Flink/Kafka Real-time Warehouse",
            description="CDC + Flink SQL real-time DWD/DWS layer",
            pros=["Sub-second latency", "Real-time dashboards and alerts"],
            cons=["High operational complexity", "Difficult data backfill", "Much higher cost"],
            recommendation="Only for sub-second SLA requirements",
        ))

    if _has_keyword(desc_lower, ["dimension", "dictionary", "product catalog", "user profile"]):
        options.append(ModelingOption(
            name="Partitioned Dimension Table",
            description="Snapshot per business date partition, SCD compatible",
            pros=["Change history support", "Partition pruning improves scan efficiency"],
            cons=["Partition strategy maintenance", "Slightly more complex ETL"],
            recommendation="For frequently changing dimensions",
        ))

    if not options:
        options.append(ModelingOption(
            name="Star Schema",
            description="Fact + dimension tables, standard Kimball methodology",
            pros=["High team acceptance", "Simple queries, clear JOIN paths"],
            cons=["Dimension tables need separate maintenance", "Complex M:N relationships"],
            recommendation="Default for most enterprise data warehouses",
        ))
        options.append(ModelingOption(
            name="Snowflake Schema",
            description="Further normalized dimensions, reduced redundancy",
            pros=["Storage savings", "High data consistency"],
            cons=["Multi-level JOINs hurt performance", "BI tool compatibility issues"],
            recommendation="Only when storage is the primary concern",
        ))

    return {
        "scenario": business_desc[:200],
        "options": [
            {"name": o.name, "description": o.description,
             "pros": o.pros, "cons": o.cons,
             "recommendation": o.recommendation}
            for o in options
        ],
        "recommended": options[0].name,
    }
