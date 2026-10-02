"""Ontology package (Phase 4 PRD)."""
from app.ontology.engine import (
    ClassNode,
    RelationEdge,
    InstanceRef,
    LineageTechnicalEdge,
    BusinessLineage,
    OntologyGraph,
    ImpactResult,
    ImpactAnalyzer,
)
from app.ontology.plan import (
    Measure,
    DimensionFilter,
    DimensionGroupBy,
    TimeRange,
    JoinSpec,
    SemanticPlan,
    render_sql_from_plan,
)

__all__ = [
    "ClassNode", "RelationEdge", "InstanceRef",
    "LineageTechnicalEdge", "BusinessLineage",
    "OntologyGraph", "ImpactResult", "ImpactAnalyzer",
    # Semantic plan (NL→IR→SQL)
    "Measure", "DimensionFilter", "DimensionGroupBy",
    "TimeRange", "JoinSpec", "SemanticPlan", "render_sql_from_plan",
]
