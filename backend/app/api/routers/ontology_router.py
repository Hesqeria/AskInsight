"""Ontology + lineage REST API (Phase 4 PRD §7).

Endpoints
---------
  Ontology:
    GET  /api/v1/ontology/classes
    GET  /api/v1/ontology/classes/{class_id}
    GET  /api/v1/ontology/instances?class_id=&table=
    GET  /api/v1/ontology/relations?src_class=

  Lineage:
    GET  /api/v1/lineage/technical?table=
    GET  /api/v1/lineage/business?term=
    GET  /api/v1/lineage/semantic?class=&instance=

  Reasoning (PRD §6):
    POST /api/v1/impact-analysis        body: { "instance_id": "..." }
    GET  /api/v1/ontology/pii/{class_id}    PII inheritance preview

All endpoints gracefully degrade when the ontology tables haven't been
created yet (return empty results + 200) so the wider service stays
healthy before the DDL is applied.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.core.auth import verify_token
from app.core.log import logger
from app.ontology import ImpactAnalyzer
from app.repositories.doris.ontology.ontology_repository import OntologyRepository

ontology_router = APIRouter()


# --------------------------------------------------------------------------- #
# Ontology - classes
# --------------------------------------------------------------------------- #
@ontology_router.get("/api/v1/ontology/classes")
async def list_classes(
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = OntologyRepository(session)
    classes = await repo.list_classes()
    return {
        "count": len(classes),
        "classes": [
            {
                "class_id": c.class_id,
                "class_name": c.class_name,
                "class_name_zh": c.class_name_zh,
                "parent_class_id": c.parent_class_id,
                "is_transitive": c.is_transitive,
                "pii_level": c.pii_level,
            }
            for c in classes
        ],
    }


@ontology_router.get("/api/v1/ontology/classes/{class_id}")
async def get_class(
    class_id: str,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Class detail + its properties + direct child classes."""
    repo = OntologyRepository(session)
    cls = await repo.get_class(class_id)
    if cls is None:
        return JSONResponse(status_code=404, content={
            "error": f"class {class_id!r} not found (has the ontology DDL been applied?)"
        })
    properties = await repo.list_properties(class_id=class_id)

    # Effective PII level (with inheritance) requires building the graph.
    classes = await repo.list_classes()
    relations = await repo.list_relations()
    from app.ontology import OntologyGraph
    graph = OntologyGraph(classes, relations)
    effective_pii = graph.effective_pii_level(class_id)

    # Children (inverse of parent_class_id).
    children = [c for c in classes if c.parent_class_id == class_id]

    return {
        "class_id": cls.class_id,
        "class_name": cls.class_name,
        "class_name_zh": cls.class_name_zh,
        "parent_class_id": cls.parent_class_id,
        "is_transitive": cls.is_transitive,
        "pii_level": cls.pii_level,
        "effective_pii_level": effective_pii,
        "children": [
            {"class_id": c.class_id, "class_name": c.class_name,
             "class_name_zh": c.class_name_zh}
            for c in children
        ],
        "properties": properties,
    }


# --------------------------------------------------------------------------- #
# Ontology - instances
# --------------------------------------------------------------------------- #
@ontology_router.get("/api/v1/ontology/instances")
async def list_instances(
    class_id: Optional[str] = None,
    table: Optional[str] = None,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = OntologyRepository(session)
    instances = await repo.list_instances(class_id=class_id, table_name=table)
    return {
        "count": len(instances),
        "instances": [
            {
                "instance_id": i.instance_id,
                "db_name": i.db_name,
                "table_name": i.table_name,
                "column_name": i.column_name,
                "class_id": i.class_id,
                "property_id": i.property_id,
                "role": i.role,
            }
            for i in instances
        ],
    }


# --------------------------------------------------------------------------- #
# Ontology - relations
# --------------------------------------------------------------------------- #
@ontology_router.get("/api/v1/ontology/relations")
async def list_relations(
    src_class: Optional[str] = None,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = OntologyRepository(session)
    if src_class:
        edges = await repo.list_relations(src_class_id=src_class)
        return {
            "count": len(edges),
            "relations": [
                {
                    "src_class_id": e.src_class_id,
                    "dst_class_id": e.dst_class_id,
                    "relation_type": e.relation_type,
                    "cardinality": e.cardinality,
                    "is_transitive": e.is_transitive,
                    "is_symmetric": e.is_symmetric,
                }
                for e in edges
            ],
        }
    # Full list with joined class names.
    rows = await repo.list_all_relations_with_meta()
    return {"count": len(rows), "relations": rows}


# --------------------------------------------------------------------------- #
# Lineage - technical
# --------------------------------------------------------------------------- #
@ontology_router.get("/api/v1/lineage/technical")
async def get_technical_lineage(
    table: Optional[str] = None,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = OntologyRepository(session)
    edges = await repo.list_technical_lineage(table_name=table)
    return {
        "count": len(edges),
        "edges": [
            {
                "lineage_id": e.lineage_id,
                "src": f"{e.src_db}.{e.src_table}" + (f".{e.src_column}" if e.src_column else ""),
                "dst": f"{e.dst_db}.{e.dst_table}" + (f".{e.dst_column}" if e.dst_column else ""),
                "transformation": e.transformation,
            }
            for e in edges
        ],
    }


# --------------------------------------------------------------------------- #
# Lineage - business (term -> physical fields)
# --------------------------------------------------------------------------- #
@ontology_router.get("/api/v1/lineage/business")
async def get_business_lineage(
    term: Optional[str] = None,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = OntologyRepository(session)
    rows = await repo.list_all_business_lineage_meta()
    if term:
        import json
        rows = [r for r in rows if r.get("term_name") == term]
    return {"count": len(rows), "metrics": rows}


# --------------------------------------------------------------------------- #
# Lineage - semantic
# --------------------------------------------------------------------------- #
@ontology_router.get("/api/v1/lineage/semantic")
async def get_semantic_lineage(
    src_class: Optional[str] = Query(None, alias="class"),
    instance: Optional[str] = None,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    repo = OntologyRepository(session)
    rows = await repo.list_semantic_lineage(
        src_class_id=src_class, src_instance_id=instance,
    )
    return {"count": len(rows), "inferences": rows}


# --------------------------------------------------------------------------- #
# Impact analysis (PRD §6.3)
# --------------------------------------------------------------------------- #
class ImpactRequest(BaseModel):
    instance_id: str
    max_depth: int = 8


@ontology_router.post("/api/v1/impact-analysis")
async def impact_analysis(
    body: ImpactRequest,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """BFS over the merged technical + business + ontology graph.

    Returns the full downstream impact set when changing/dropping the
    input instance_id (e.g. dropping `dw.dim_sku_info.category3_id`).

    The whole graph is loaded in memory per request. At the PRD's 55
    table / 800 field scale this is well under 200ms (per PRD §6.3)."""
    repo = OntologyRepository(session)
    graph, instances, tech, biz = await repo.load_impact_analyzer_inputs()
    if not instances:
        return JSONResponse(status_code=404, content={
            "error": f"instance {body.instance_id!r} not found, "
                     "or ontology tables empty (apply ontology_schema.sql + ontology_seed.sql)"
        })
    analyzer = ImpactAnalyzer(graph, instances, tech, biz)
    result = analyzer.analyze(body.instance_id, max_depth=body.max_depth)
    logger.info(
        f"impact-analysis start={body.instance_id!r} "
        f"impacted={result.to_dict()['impact_count']}"
    )
    return result.to_dict()


# --------------------------------------------------------------------------- #
# PII inheritance preview
# --------------------------------------------------------------------------- #
@ontology_router.get("/api/v1/ontology/pii/{class_id}")
async def get_pii_inheritance(
    class_id: str,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Show the effective PII level of a class after walking up the
    parent_class_id hierarchy (PRD §6.2)."""
    repo = OntologyRepository(session)
    classes = await repo.list_classes()
    relations = await repo.list_relations()
    from app.ontology import OntologyGraph
    graph = OntologyGraph(classes, relations)
    cls = graph.classes.get(class_id)
    if cls is None:
        return JSONResponse(status_code=404, content={
            "error": f"class {class_id!r} not found"
        })
    ancestors = graph.ancestors(class_id)
    return {
        "class_id": class_id,
        "class_name": cls.class_name,
        "own_pii_level": cls.pii_level,
        "effective_pii_level": graph.effective_pii_level(class_id),
        "ancestor_chain": ancestors,
    }


# --------------------------------------------------------------------------- #
# LLM lineage parsing (P3-06)
# --------------------------------------------------------------------------- #
class LineageParseRequest(BaseModel):
    code: str
    type: str = "sql"   # sql / python / shell
    persist: bool = True
    use_llm: bool = True


@ontology_router.post("/api/v1/lineage/parse")
async def parse_lineage(
    body: LineageParseRequest,
    user: dict = Depends(verify_token),
    session: AsyncSession = Depends(get_meta_session),
):
    """Parse Python/SQL/Shell ETL code into column-level technical
    lineage and (optionally) persist to `lineage_technical`.

    Flow:
      1. Run LineageParser (rule-based baseline always; LLM if wired)
      2. Optionally persist edges via OntologyRepository
      3. Return the parsed edges + confidence signal

    Failures degrade gracefully: if the LLM call times out, the rule-
    based SQL fallback is returned. If persist fails, we still return
    the parsed edges (the response includes the persist_ok flag)."""
    from app.agents.lineage_agent import LineageParser
    from app.infra.llm_router import get_llm

    # Try to wire the LLM; if the router isn't initialized the parser
    # just runs its rule-based fallback.
    try:
        llm = get_llm()
    except Exception:
        llm = None

    parser = LineageParser(llm=llm if body.use_llm else None)
    try:
        edges = await parser.parse(body.code, code_type=body.type)
    except Exception as e:
        logger.error(f"lineage parse failed: {e}")
        return JSONResponse(status_code=500, content={
            "status": "error", "message": str(e), "edges": [],
        })

    persist_ok = False
    persisted_count = 0
    if body.persist and edges:
        repo = OntologyRepository(session)
        source_label = "LLM" if (body.use_llm and llm is not None) else "RULE"
        try:
            persisted_count = await repo.upsert_technical_lineage_batch(
                edges, source=source_label,
            )
            persist_ok = True
        except Exception as e:
            logger.warning(f"lineage persist failed: {e}")

    logger.info(
        f"lineage parse type={body.type} parsed={len(edges)} "
        f"persisted={persisted_count}"
    )
    return {
        "status": "ok",
        "code_type": body.type,
        "edge_count": len(edges),
        "persist_ok": persist_ok,
        "persisted_count": persisted_count,
        "used_llm": body.use_llm and llm is not None,
        "edges": [
            {
                "lineage_id": e.lineage_id,
                "src": f"{e.src_db}.{e.src_table}" + (f".{e.src_column}" if e.src_column else ""),
                "dst": f"{e.dst_db}.{e.dst_table}" + (f".{e.dst_column}" if e.dst_column else ""),
                "transformation": e.transformation,
            }
            for e in edges
        ],
    }
