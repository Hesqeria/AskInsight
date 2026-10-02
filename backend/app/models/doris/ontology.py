"""SQLAlchemy ORM models for the ontology tables (Phase 4 PRD).

We keep these intentionally close to the DDL in conf/ddl/ontology_schema.sql
so the column names + types line up 1:1. Queries against these tables
mostly happen through `OntologyRepository` raw SQL (for transitive
closure and impact analysis) - the ORM is used for simple CRUD.
"""
from sqlalchemy import String, Text, Boolean, Integer, Float, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.doris.base import Base


class OntClass(Base):
    __tablename__ = "ont_class"

    class_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    class_name: Mapped[str] = mapped_column(String(64))
    class_name_zh: Mapped[str] = mapped_column(String(64))
    parent_class_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_transitive: Mapped[bool] = mapped_column(Boolean, default=False)
    pii_level: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OntProperty(Base):
    __tablename__ = "ont_property"

    property_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    property_name: Mapped[str] = mapped_column(String(64))
    property_name_zh: Mapped[str] = mapped_column(String(64))
    class_id: Mapped[str] = mapped_column(String(64))
    data_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_pk: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fk: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pii: Mapped[bool] = mapped_column(Boolean, default=False)
    business_term: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class OntRelation(Base):
    __tablename__ = "ont_relation"

    relation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    src_class_id: Mapped[str] = mapped_column(String(64))
    dst_class_id: Mapped[str] = mapped_column(String(64))
    relation_type: Mapped[str] = mapped_column(String(64))
    relation_type_zh: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cardinality: Mapped[str] = mapped_column(String(8), default="N:1")
    is_transitive: Mapped[bool] = mapped_column(Boolean, default=False)
    is_symmetric: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(16), default="MANUAL")


class OntInstance(Base):
    __tablename__ = "ont_instance"

    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    db_name: Mapped[str] = mapped_column(String(32))
    table_name: Mapped[str] = mapped_column(String(64))
    column_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    class_id: Mapped[str] = mapped_column(String(64))
    property_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(16), default="MANUAL")


class LineageTechnical(Base):
    __tablename__ = "lineage_technical"

    lineage_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    src_db: Mapped[str] = mapped_column(String(32))
    src_table: Mapped[str] = mapped_column(String(64))
    src_column: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dst_db: Mapped[str] = mapped_column(String(32))
    dst_table: Mapped[str] = mapped_column(String(64))
    dst_column: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transformation: Mapped[str] = mapped_column(String(32))
    transform_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    etl_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(16), default="SQL_PARSE")


class LineageBusiness(Base):
    __tablename__ = "lineage_business"

    business_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    term_name: Mapped[str] = mapped_column(String(128))
    metric_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sql_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_tables: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_columns: Mapped[str | None] = mapped_column(Text, nullable=True)
    filters: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class LineageSemantic(Base):
    __tablename__ = "lineage_semantic"

    semantic_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    src_class_id: Mapped[str] = mapped_column(String(64))
    src_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dst_class_id: Mapped[str] = mapped_column(String(64))
    relation_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_type: Mapped[str] = mapped_column(String(32))
    impact_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(16), default="INFERENCE")
