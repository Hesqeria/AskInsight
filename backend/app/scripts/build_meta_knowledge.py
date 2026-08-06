"""Knowledge-base build script: syncs meta_config.yaml -> Doris meta tables + Milvus collection.
Usage: python -m app.scripts.build_meta_knowledge -c ./conf/meta_config.yaml
"""
import argparse
import asyncio
import uuid
from pathlib import Path

from omegaconf import OmegaConf
from pymilvus import CollectionSchema, FieldSchema, DataType
from sqlalchemy import text

from app.clients.doris_client_manager import doris_client_manager
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.milvus_client_manager import milvus_client_manager
from app.conf.app_config import app_config
from app.conf.meta_config import MetaConfig
from app.core.log import logger
from app.models.doris.column_info import ColumnInfoDoris
from app.models.doris.column_metric import ColumnMetricDoris
from app.models.doris.metric_info import MetricInfoDoris
from app.models.doris.table_info import TableInfoDoris


async def build(config_path: Path):
    ctx = OmegaConf.load(config_path)
    schema = OmegaConf.structured(MetaConfig)
    meta_cfg: MetaConfig = OmegaConf.to_object(OmegaConf.merge(schema, ctx))
    logger.info(f"Config loaded: {len(meta_cfg.tables or [])} tables, {len(meta_cfg.metrics or [])} metrics")

    doris_client_manager.init()
    embedding_client_manager.init()
    milvus_client_manager.init()
    if not milvus_client_manager.connected:
        logger.warning("Milvus connection failed; skipping Milvus build (Doris will continue)")

    # === Doris metadata ===
    column_infos = []
    async with doris_client_manager.session_factory() as session:
        for t in ["column_metric", "metric_info", "column_info", "table_info", "column_value_info"]:
            await session.execute(text(f"TRUNCATE TABLE {t}"))
        await session.commit()

        if meta_cfg.tables:
            tables = []
            for tb in meta_cfg.tables:
                tables.append(TableInfoDoris(id=tb.name, name=tb.name, role=tb.role, description=tb.description))
                for c in tb.columns:
                    column_infos.append(ColumnInfoDoris(
                        id=f"{tb.name}.{c.name}", name=c.name, type="", role=c.role,
                        examples=[], description=c.description, alias=c.alias, table_id=tb.name))
            session.add_all(tables)
            session.add_all(column_infos)
            await session.commit()
            logger.info(f"Doris: {len(tables)} tables, {len(column_infos)} columns")

    # === column_value_info (dimension-value inverted index) ===
    if meta_cfg.tables:
        sync_cols = [(tb.name, c.name) for tb in meta_cfg.tables for c in tb.columns if c.sync]
        async with doris_client_manager.session_factory() as session:
            rows = []
            for tn, cn in sync_cols:
                rs = await session.execute(text(f"SELECT DISTINCT `{cn}` FROM {tn} LIMIT 1000"))
                for r in rs.fetchall():
                    v = str(r[0]) if r[0] is not None else ""
                    if v:
                        rows.append((f"{tn}.{cn}.{v}", v, "dimension", f"{tn}.{cn}", cn, tn, tn))
            if rows:
                sql = "INSERT INTO column_value_info (id,value,type,column_id,column_name,table_id,table_name) VALUES (:id,:value,:type,:column_id,:column_name,:table_id,:table_name)"
                for row in rows:
                    await session.execute(text(sql), {"id": row[0],"value": row[1],"type": row[2],"column_id": row[3],"column_name": row[4],"table_id": row[5],"table_name": row[6]})
                await session.commit()
                logger.info(f"Doris column_value_info: {len(rows)} rows")

    # === Metrics ===
    metrics, col_metrics = [], []
    if meta_cfg.metrics:
        for m in meta_cfg.metrics:
            metrics.append(MetricInfoDoris(id=m.name, name=m.name, description=m.description, relevant_columns=m.relevant_columns, alias=m.alias))
            for rc in m.relevant_columns:
                col_metrics.append(ColumnMetricDoris(column_id=rc, metric_id=m.name))
        async with doris_client_manager.session_factory() as session:
            session.add_all(metrics)
            session.add_all(col_metrics)
            await session.commit()
        logger.info(f"Doris: {len(metrics)} metrics, {len(col_metrics)} associations")

    # === Milvus ===
    try:
        _build_milvus(column_infos, is_column=True)
        _build_milvus(metrics, is_column=False)
    except Exception as e:
        logger.warning(f"Milvus build failed (Doris unaffected): {e}")

    await doris_client_manager.close()
    logger.info("Knowledge-base build completed")


def _build_milvus(items, is_column: bool):
    client = milvus_client_manager.client
    emb = embedding_client_manager.client
    col_name = app_config.milvus.column_collection if is_column else app_config.milvus.metric_collection
    dim = app_config.milvus.embedding_size

    if client.has_collection(col_name):
        client.drop_collection(col_name)

    if is_column:
        schema = CollectionSchema(fields=[
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="role", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="examples", dtype=DataType.JSON),
            FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="alias", dtype=DataType.JSON),
            FieldSchema(name="table_id", dtype=DataType.VARCHAR, max_length=64),
        ])
    else:
        schema = CollectionSchema(fields=[
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="relevant_columns", dtype=DataType.JSON),
            FieldSchema(name="alias", dtype=DataType.JSON),
        ])
    client.create_collection(col_name, schema=schema)
    from pymilvus.milvus_client.index import IndexParams
    idx_params = IndexParams()
    idx_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_index(col_name, index_params=idx_params)
    client.load_collection(col_name)

    # Build text to be vectorized
    texts, metas = [], []
    for it in items:
        if is_column:
            meta = {"id": it.id, "name": it.name, "type": it.type, "role": it.role,
                    "examples": it.examples or [], "description": it.description or "",
                    "alias": it.alias or [], "table_id": it.table_id}
            candidates = [it.name, it.description] + list(it.alias or [])
        else:
            meta = {"id": it.id, "name": it.name, "description": it.description or "",
                    "relevant_columns": it.relevant_columns or [], "alias": it.alias or []}
            candidates = [it.name, it.description] + list(it.alias or [])
        for txt in candidates:
            if txt:
                texts.append(txt)
                metas.append(meta)

    batch = 10
    total = 0
    for i in range(0, len(texts), batch):
        embs = emb.embed_documents(texts[i:i+batch])  # OpenAIEmbeddings sync method
        data = []
        for j, e in enumerate(embs):
            m = metas[i+j]
            m["id"] = str(uuid.uuid4())
            m["vector"] = e
            data.append(m)
        client.insert(col_name, data=data)
        total += len(data)
    logger.info(f"Milvus {col_name}: {total} vectors")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--conf", required=True)
    args = parser.parse_args()
    asyncio.run(build(Path(args.conf)))
