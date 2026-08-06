"""Knowledge-base incremental update service.

Strategy:
  1. Metadata (table_info/column_info/metric_info): UPSERT (update if exists, insert if not)
  2. Dimension values (column_value_info): only sync new values (deduplicate against existing)
  3. Vector index (Milvus): only re-vectorize changed fields

Usage:
  python -m app.scripts.incremental_update -c ./conf/meta_config.yaml
  python -m app.scripts.incremental_update -c ./conf/meta_config.yaml --table dim_product  # only update the specified table
"""
import argparse
import asyncio
import uuid
from pathlib import Path

from omegaconf import OmegaConf
from sqlalchemy import text

from app.clients.doris_client_manager import doris_client_manager
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.milvus_client_manager import milvus_client_manager
from app.conf.app_config import app_config
from app.conf.meta_config import MetaConfig
from app.core.log import logger
from app.models.doris.column_info import ColumnInfoDoris
from app.models.doris.table_info import TableInfoDoris


async def incremental_update(config_path: Path, table_filter: str = None):
    """Incrementally update the knowledge base (no full rebuild).

    Args:
        config_path: path to meta_config.yaml
        table_filter: only update the specified table name (None = all)
    """
    ctx = OmegaConf.load(config_path)
    schema = OmegaConf.structured(MetaConfig)
    meta_cfg: MetaConfig = OmegaConf.to_object(OmegaConf.merge(schema, ctx))

    doris_client_manager.init()
    embedding_client_manager.init()
    milvus_client_manager.init()

    # Filter tables
    tables_to_update = meta_cfg.tables or []
    if table_filter:
        tables_to_update = [t for t in tables_to_update if t.name == table_filter]

    logger.info(f"Incremental update: {len(tables_to_update)} tables")

    stats = {"tables": 0, "columns": 0, "values": 0, "vectors": 0, "skipped": 0}

    async with doris_client_manager.session_factory() as session:
        for tb in tables_to_update:
            # 1. UPSERT table_info
            existing = await session.get(TableInfoDoris, tb.name)
            if existing:
                existing.name = tb.name
                existing.role = tb.role
                existing.description = tb.description
                stats["skipped"] += 0  # update
            else:
                session.add(TableInfoDoris(id=tb.name, name=tb.name, role=tb.role, description=tb.description))
                stats["tables"] += 1

            # 2. UPSERT column_info + detect changes
            for c in tb.columns:
                col_id = f"{tb.name}.{c.name}"
                existing_col = await session.get(ColumnInfoDoris, col_id)
                need_revectorize = False

                if existing_col:
                    # Detect whether description/alias changed
                    if existing_col.description != c.description or existing_col.alias != c.alias:
                        existing_col.description = c.description
                        existing_col.alias = c.alias
                        existing_col.role = c.role
                        need_revectorize = True
                        stats["columns"] += 1
                else:
                    # New field
                    session.add(ColumnInfoDoris(
                        id=col_id, name=c.name, type="", role=c.role,
                        examples=[], description=c.description, alias=c.alias, table_id=tb.name
                    ))
                    need_revectorize = True
                    stats["columns"] += 1

                # 3. Incrementally sync dimension values (only add new values)
                if c.sync:
                    new_values = await _sync_new_values(session, tb.name, c.name, col_id)
                    stats["values"] += new_values

                # 4. Re-vectorize changed fields (Milvus)
                if need_revectorize and milvus_client_manager.connected:
                    await _revectorize_column(col_id, c.name, c.description, c.alias)
                    stats["vectors"] += 1

        await session.commit()

    logger.info(f"Incremental update completed: tables={stats['tables']}, column_changes={stats['columns']}, "
                f"new_dimension_values={stats['values']}, revectorized={stats['vectors']}")

    await doris_client_manager.close()


async def _sync_new_values(session, table_name: str, col_name: str, col_id: str) -> int:
    """Incrementally sync dimension values: only insert values not yet in column_value_info."""
    # Query all distinct values for this field in the DW
    result = await session.execute(
        text(f"SELECT DISTINCT `{col_name}` FROM {table_name} LIMIT 1000")
    )
    dw_values = {str(r[0]) for r in result.fetchall() if r[0] is not None}

    if not dw_values:
        return 0

    # Query existing values for this field in meta
    result = await session.execute(
        text("SELECT value FROM column_value_info WHERE column_id = :cid"),
        {"cid": col_id}
    )
    meta_values = {r[0] for r in result.fetchall()}

    # Compute new values
    new_values = dw_values - meta_values
    if not new_values:
        return 0

    # Insert new values
    for v in new_values:
        val_id = f"{col_id}.{v}"
        await session.execute(
            text("INSERT INTO column_value_info (id, value, type, column_id, column_name, table_id, table_name) "
                 "VALUES (:id, :val, 'dimension', :cid, :cn, :tid, :tn)"),
            {"id": val_id, "val": v, "cid": col_id, "cn": col_name, "tid": table_name, "tn": table_name}
        )

    return len(new_values)


async def _revectorize_column(col_id: str, name: str, description: str, alias: list):
    """Re-vectorize changed fields."""
    try:
        client = milvus_client_manager.client
        emb = embedding_client_manager.client
        col_name = app_config.milvus.column_collection

        # Delete old vectors for this field first
        client.delete(
            collection_name=col_name,
            filter=f"id == '{col_id}'"
        )

        # Regenerate vectors and insert
        texts = [name, description] + list(alias or [])
        texts = [t for t in texts if t]
        if not texts:
            return

        embeddings = emb.embed_documents(texts)
        data = []
        for i, (txt, vec) in enumerate(zip(texts, embeddings)):
            data.append({
                "id": str(uuid.uuid4()),
                "vector": vec,
                "name": name,
                "type": "",
                "role": "updated",
                "examples": [],
                "description": description,
                "alias": alias or [],
                "table_id": col_id.split(".")[0] if "." in col_id else "",
            })
        client.insert(collection_name=col_name, data=data)
        logger.info(f"Field {col_id} re-vectorized: {len(texts)} items")

    except Exception as e:
        logger.warning(f"Field {col_id} vectorization failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Knowledge-base incremental update")
    parser.add_argument("-c", "--conf", required=True, help="Path to meta_config.yaml")
    parser.add_argument("--table", default=None, help="Only update the specified table")
    args = parser.parse_args()
    asyncio.run(incremental_update(Path(args.conf), args.table))
