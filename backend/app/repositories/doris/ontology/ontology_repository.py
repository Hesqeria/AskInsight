"""Ontology repository.

Loads raw rows from the seven ontology/lineage tables into the pure-dataclass
shapes the in-memory reasoning engine expects. All methods are defensive:
on any DB error they return empty collections so callers degrade gracefully
when the DDL hasn't been applied yet (matches the pattern in rl_doris_repository).
"""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger
from app.ontology import (
    ClassNode, RelationEdge, InstanceRef,
    LineageTechnicalEdge, BusinessLineage,
)


class OntologyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------ #
    # ont_class
    # ------------------------------------------------------------------ #
    async def list_classes(self) -> list[ClassNode]:
        try:
            rows = await self.session.execute(text(
                "SELECT class_id, class_name, class_name_zh, parent_class_id, "
                "is_transitive, pii_level FROM data_agent.ont_class"
            ))
            return [
                ClassNode(
                    class_id=r[0], class_name=r[1], class_name_zh=r[2],
                    parent_class_id=r[3], is_transitive=bool(r[4]),
                    pii_level=int(r[5] or 0),
                )
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"ont_class load failed: {e}")
            return []

    async def get_class(self, class_id: str) -> Optional[ClassNode]:
        try:
            rows = await self.session.execute(text(
                "SELECT class_id, class_name, class_name_zh, parent_class_id, "
                "is_transitive, pii_level FROM data_agent.ont_class WHERE class_id = :cid"
            ), {"cid": class_id})
            r = rows.fetchone()
            if not r:
                return None
            return ClassNode(
                class_id=r[0], class_name=r[1], class_name_zh=r[2],
                parent_class_id=r[3], is_transitive=bool(r[4]),
                pii_level=int(r[5] or 0),
            )
        except Exception as e:
            logger.warning(f"ont_class get failed: {e}")
            return None

    async def list_properties(self, class_id: Optional[str] = None) -> list[dict]:
        try:
            if class_id:
                rows = await self.session.execute(text(
                    "SELECT property_id, property_name, property_name_zh, class_id, "
                    "data_type, is_pk, is_fk, is_pii, business_term, description "
                    "FROM data_agent.ont_property WHERE class_id = :cid"
                ), {"cid": class_id})
            else:
                rows = await self.session.execute(text(
                    "SELECT property_id, property_name, property_name_zh, class_id, "
                    "data_type, is_pk, is_fk, is_pii, business_term, description "
                    "FROM data_agent.ont_property"
                ))
            return [
                {
                    "property_id": r[0], "property_name": r[1],
                    "property_name_zh": r[2], "class_id": r[3],
                    "data_type": r[4], "is_pk": bool(r[5]),
                    "is_fk": bool(r[6]), "is_pii": bool(r[7]),
                    "business_term": r[8], "description": r[9],
                }
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"ont_property load failed: {e}")
            return []

    # ------------------------------------------------------------------ #
    # ont_relation
    # ------------------------------------------------------------------ #
    async def list_relations(
        self, src_class_id: Optional[str] = None,
    ) -> list[RelationEdge]:
        try:
            if src_class_id:
                rows = await self.session.execute(text(
                    "SELECT src_class_id, dst_class_id, relation_type, "
                    "cardinality, is_transitive, is_symmetric "
                    "FROM data_agent.ont_relation WHERE src_class_id = :cid"
                ), {"cid": src_class_id})
            else:
                rows = await self.session.execute(text(
                    "SELECT src_class_id, dst_class_id, relation_type, "
                    "cardinality, is_transitive, is_symmetric FROM data_agent.ont_relation"
                ))
            return [
                RelationEdge(
                    src_class_id=r[0], dst_class_id=r[1],
                    relation_type=r[2], cardinality=r[3] or "N:1",
                    is_transitive=bool(r[4]), is_symmetric=bool(r[5]),
                )
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"ont_relation load failed: {e}")
            return []

    async def list_all_relations_with_meta(self) -> list[dict]:
        """Join with ont_class for richer API responses."""
        try:
            rows = await self.session.execute(text(
                "SELECT r.relation_id, r.src_class_id, r.dst_class_id, "
                "r.relation_type, r.relation_type_zh, r.cardinality, "
                "r.is_transitive, r.is_symmetric, r.confidence, r.source, "
                "s.class_name AS src_name, d.class_name AS dst_name "
                "FROM data_agent.ont_relation r "
                "LEFT JOIN data_agent.ont_class s ON s.class_id = r.src_class_id "
                "LEFT JOIN data_agent.ont_class d ON d.class_id = r.dst_class_id"
            ))
            return [
                {
                    "relation_id": r[0], "src_class_id": r[1],
                    "dst_class_id": r[2], "relation_type": r[3],
                    "relation_type_zh": r[4], "cardinality": r[5],
                    "is_transitive": bool(r[6]), "is_symmetric": bool(r[7]),
                    "confidence": float(r[8] or 1.0), "source": r[9],
                    "src_class_name": r[10], "dst_class_name": r[11],
                }
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"ont_relation join load failed: {e}")
            return []

    # ------------------------------------------------------------------ #
    # ont_instance
    # ------------------------------------------------------------------ #
    async def list_instances(
        self, class_id: Optional[str] = None,
        table_name: Optional[str] = None,
    ) -> list[InstanceRef]:
        clauses = []
        params: dict = {}
        if class_id:
            clauses.append("class_id = :cid")
            params["cid"] = class_id
        if table_name:
            clauses.append("table_name = :tbl")
            params["tbl"] = table_name
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            rows = await self.session.execute(text(
                "SELECT instance_id, db_name, table_name, column_name, "
                "class_id, property_id, `role` FROM data_agent.ont_instance" + where
            ), params)
            return [
                InstanceRef(
                    instance_id=r[0], db_name=r[1], table_name=r[2],
                    column_name=r[3], class_id=r[4],
                    property_id=r[5], role=r[6],
                )
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"ont_instance load failed: {e}")
            return []

    async def get_instance(self, instance_id: str) -> Optional[InstanceRef]:
        try:
            rows = await self.session.execute(text(
                "SELECT instance_id, db_name, table_name, column_name, "
                "class_id, property_id, role FROM data_agent.ont_instance "
                "WHERE instance_id = :iid"
            ), {"iid": instance_id})
            r = rows.fetchone()
            if not r:
                return None
            return InstanceRef(
                instance_id=r[0], db_name=r[1], table_name=r[2],
                column_name=r[3], class_id=r[4],
                property_id=r[5], role=r[6],
            )
        except Exception as e:
            logger.warning(f"ont_instance get failed: {e}")
            return None

    # ------------------------------------------------------------------ #
    # lineage_technical
    # ------------------------------------------------------------------ #
    async def list_technical_lineage(
        self, table_name: Optional[str] = None,
    ) -> list[LineageTechnicalEdge]:
        try:
            if table_name:
                rows = await self.session.execute(text(
                    "SELECT lineage_id, src_db, src_table, src_column, "
                    "dst_db, dst_table, dst_column, transformation "
                    "FROM data_agent.lineage_technical "
                    "WHERE src_table = :t OR dst_table = :t"
                ), {"t": table_name})
            else:
                rows = await self.session.execute(text(
                    "SELECT lineage_id, src_db, src_table, src_column, "
                    "dst_db, dst_table, dst_column, transformation "
                    "FROM data_agent.lineage_technical"
                ))
            return [
                LineageTechnicalEdge(
                    lineage_id=r[0], src_db=r[1], src_table=r[2],
                    src_column=r[3], dst_db=r[4], dst_table=r[5],
                    dst_column=r[6], transformation=r[7] or "DIRECT",
                )
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"lineage_technical load failed: {e}")
            return []

    # ------------------------------------------------------------------ #
    # lineage_business
    # ------------------------------------------------------------------ #
    async def list_business_lineage(
        self, term_name: Optional[str] = None,
    ) -> list[BusinessLineage]:
        import json
        try:
            if term_name:
                rows = await self.session.execute(text(
                    "SELECT business_id, term_name, related_columns "
                    "FROM data_agent.lineage_business WHERE term_name = :t"
                ), {"t": term_name})
            else:
                rows = await self.session.execute(text(
                    "SELECT business_id, term_name, related_columns "
                    "FROM data_agent.lineage_business"
                ))
            out = []
            for r in rows.fetchall():
                cols_json = r[2] or "[]"
                try:
                    cols = tuple(json.loads(cols_json))
                except (ValueError, TypeError):
                    cols = ()
                out.append(BusinessLineage(
                    business_id=r[0], term_name=r[1], related_columns=cols,
                ))
            return out
        except Exception as e:
            logger.warning(f"lineage_business load failed: {e}")
            return []

    async def list_all_business_lineage_meta(self) -> list[dict]:
        try:
            rows = await self.session.execute(text(
                "SELECT business_id, term_name, metric_id, sql_template, "
                "related_tables, related_columns, filters, owner, description "
                "FROM data_agent.lineage_business"
            ))
            return [
                {
                    "business_id": r[0], "term_name": r[1], "metric_id": r[2],
                    "sql_template": r[3], "related_tables": r[4],
                    "related_columns": r[5], "filters": r[6],
                    "owner": r[7], "description": r[8],
                }
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"lineage_business meta load failed: {e}")
            return []

    # ------------------------------------------------------------------ #
    # lineage_semantic
    # ------------------------------------------------------------------ #
    async def list_semantic_lineage(
        self,
        src_class_id: Optional[str] = None,
        src_instance_id: Optional[str] = None,
    ) -> list[dict]:
        clauses, params = [], {}
        if src_class_id:
            clauses.append("src_class_id = :cid")
            params["cid"] = src_class_id
        if src_instance_id:
            clauses.append("src_instance_id = :iid")
            params["iid"] = src_instance_id
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            rows = await self.session.execute(text(
                "SELECT semantic_id, src_class_id, src_instance_id, "
                "dst_class_id, relation_path, impact_type, impact_desc, "
                "confidence, source FROM data_agent.lineage_semantic" + where
            ), params)
            return [
                {
                    "semantic_id": r[0], "src_class_id": r[1],
                    "src_instance_id": r[2], "dst_class_id": r[3],
                    "relation_path": r[4], "impact_type": r[5],
                    "impact_desc": r[6], "confidence": float(r[7] or 1.0),
                    "source": r[8],
                }
                for r in rows.fetchall()
            ]
        except Exception as e:
            logger.warning(f"lineage_semantic load failed: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Bulk loaders (for the in-memory impact analyzer)
    # ------------------------------------------------------------------ #
    async def load_impact_analyzer_inputs(self):
        """Convenience: load everything the ImpactAnalyzer needs in
        parallel-friendly chunks. Returns (graph, instances, tech, biz)
        ready to feed into ImpactAnalyzer."""
        classes = await self.list_classes()
        relations = await self.list_relations()
        instances = await self.list_instances()
        tech = await self.list_technical_lineage()
        biz = await self.list_business_lineage()

        from app.ontology import OntologyGraph
        graph = OntologyGraph(classes, relations)
        return graph, instances, tech, biz

    # ------------------------------------------------------------------ #
    # Writes (for the LLM lineage parser / P3-06)
    # ------------------------------------------------------------------ #
    async def upsert_technical_lineage_batch(
        self, edges, source: str = "LLM",
    ) -> int:
        """Persist a batch of LineageTechnicalEdge rows. Existing rows
        (same lineage_id) are delete-then-inserted to keep semantics
        idempotent across re-parses. Returns the number of rows written.

        Failures are logged but do not raise - lineage parsing is a
        best-effort enrichment, not a critical path.
        """
        if not edges:
            return 0
        from datetime import datetime
        written = 0
        try:
            # Single-pass delete of any existing rows with the same IDs.
            ids = tuple(e.lineage_id for e in edges)
            # Doris doesn't support IN with bound params cleanly across
            # all versions; build a literal list (small N - parsed edges
            # per script rarely exceeds ~50).
            if ids:
                placeholders = ",".join(f":id{i}" for i in range(len(ids)))
                params = {f"id{i}": v for i, v in enumerate(ids)}
                await self.session.execute(
                    text(f"DELETE FROM data_agent.lineage_technical WHERE lineage_id IN ({placeholders})"),
                    params,
                )
            for e in edges:
                await self.session.execute(text("""
                    INSERT INTO data_agent.lineage_technical
                        (lineage_id, src_db, src_table, src_column,
                         dst_db, dst_table, dst_column,
                         transformation, transform_expr,
                         etl_task_id, confidence, source, updated_at)
                    VALUES (:lid, :sdb, :st, :sc, :ddb, :dt, :dc,
                            :tf, NULL, NULL, 1.000, :src, :ts)
                """), {
                    "lid": e.lineage_id,
                    "sdb": e.src_db, "st": e.src_table, "sc": e.src_column,
                    "ddb": e.dst_db, "dt": e.dst_table, "dc": e.dst_column,
                    "tf": e.transformation,
                    "src": source,
                    "ts": datetime.now(),
                })
                written += 1
            await self.session.commit()
        except Exception as e:
            logger.warning(f"lineage_technical batch upsert failed: {e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
        return written
