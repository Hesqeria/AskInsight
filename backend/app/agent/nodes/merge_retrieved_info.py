"""Three-path recall RRF fusion node

RRF (Reciprocal Rank Fusion) algorithm:
  score(d) = Σ 1/(k + rank_i(d))
  where k=60 (standard constant) and rank_i(d) is the rank of document d in the i-th retrieval path

Fuses 3 paths:
  Path 1: column vector recall (Milvus Dense) -> column-level rank
  Path 2: metric vector recall (Milvus Dense) -> metric-related column rank
  Path 3: column value full-text recall (Doris MATCH) -> value-corresponding column rank
"""
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, TableInfoState, ColumnInfoState, MetricInfoState
from app.core.log import logger
from app.models.doris.column_info import ColumnInfoDoris
from app.models.milvus.column_info_milvus import ColumnInfoMilvus
from app.models.milvus.metric_info_milvus import MetricInfoMilvus

# RRF constant (standard value, from the paper "Reciprocal Rank Fusion")
RRF_K = 60


def _col_doris_to_milvus(c: ColumnInfoDoris) -> ColumnInfoMilvus:
    return ColumnInfoMilvus(
        id=c.id, name=c.name, type=c.type, role=c.role,
        examples=c.examples or [], description=c.description or "",
        alias=c.alias or [], table_id=c.table_id,
    )


def _col_milvus_to_state(c: ColumnInfoMilvus) -> ColumnInfoState:
    return ColumnInfoState(
        name=c["name"], type=c["type"], role=c["role"],
        examples=c["examples"], description=c["description"], alias=c["alias"],
    )


def _metric_milvus_to_state(m: MetricInfoMilvus) -> MetricInfoState:
    return MetricInfoState(
        name=m["name"], description=m["description"],
        relevant_columns=m["relevant_columns"], alias=m["alias"],
    )


def _rrf_score(ranks: list[int]) -> float:
    """Compute RRF score: multi-path rank fusion

    Args:
        ranks: list of the document ranks in each path (0-based), e.g. [0, 2] means rank 1 in path 1, rank 3 in path 2

    Returns:
        RRF score (higher is better)
    """
    return sum(1.0 / (RRF_K + r) for r in ranks)


async def merge_retrieved_info(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "RRF Fusion Recall"})
    try:
        meta_repo = runtime.context["meta_doris_repository"]
        retrieved_columns = state.get("retrieved_columns", [])
        retrieved_values = state.get("retrieved_values", [])
        retrieved_metrics = state.get("retrieved_metrics", [])

        # ===== RRF scoring =====
        # Collect the rank of each column ID in each path
        column_ranks: dict[str, list[int]] = {}  # {column_id: [rank_path1, rank_path3, ...]}
        column_data: dict[str, ColumnInfoMilvus] = {}  # {column_id: column data}

        # Path 1: column vector recall
        for rank, col in enumerate(retrieved_columns):
            cid = col.get("id")
            if not cid:
                continue
            # Use the _rank returned by Milvus, or fall back to list order
            r = col.get("_rank", rank)
            column_ranks.setdefault(cid, []).append(r)
            column_data[cid] = col

        # Path 2: metric vector recall -> extract related columns
        for rank, m in enumerate(retrieved_metrics):
            for rc in m.get("relevant_columns", []):
                # The column rank from metric recall is based on the metric rank
                m_rank = m.get("_rank", rank)
                column_ranks.setdefault(rc, []).append(m_rank)
                # If the column data is missing, supplement it from Doris
                if rc not in column_data:
                    cinfo = await meta_repo.get_column_info_by_id(rc)
                    if cinfo:
                        column_data[rc] = _col_doris_to_milvus(cinfo)

        # Path 3: column value full-text recall -> value-corresponding column
        for rank, v in enumerate(retrieved_values):
            cid = v.get("column_id")
            value = v.get("value")
            if not cid:
                continue
            # The column rank from value recall
            column_ranks.setdefault(cid, []).append(rank)
            # Supplement column data
            if cid not in column_data:
                cinfo = await meta_repo.get_column_info_by_id(cid)
                if cinfo:
                    column_data[cid] = _col_doris_to_milvus(cinfo)
            # Write the value into column examples
            if cid in column_data and value not in column_data[cid]["examples"]:
                column_data[cid]["examples"].append(value)

        # ===== Compute RRF score and sort =====
        column_scores = {}
        for cid, ranks in column_ranks.items():
            score = _rrf_score(ranks)
            column_scores[cid] = score
            # Record hit-path count and score into the column data
            if cid in column_data:
                column_data[cid]["_rrf_score"] = round(score, 4)
                column_data[cid]["_hit_paths"] = len(ranks)

        # Sort by RRF score in descending order
        sorted_ids = sorted(column_scores.keys(), key=lambda x: -column_scores[x])
        logger.info(f"RRF fusion: {len(sorted_ids)} columns,"
                    f"TOP3: {[(cid[:20], round(column_scores[cid], 4)) for cid in sorted_ids[:3]]}")

        # ===== Group by table (preserving RRF order) =====
        table_to_cols: dict[str, list] = {}
        for cid in sorted_ids:
            col = column_data.get(cid)
            if not col:
                continue
            tid = col["table_id"]
            table_to_cols.setdefault(tid, []).append(col)

        # Supplement primary/foreign keys for each table
        for tid in list(table_to_cols.keys()):
            key_cols = await meta_repo.get_key_columns_by_table_id(tid)
            existing_ids = {c["id"] for c in table_to_cols[tid]}
            for kc in key_cols:
                if kc.id not in existing_ids:
                    table_to_cols[tid].append(_col_doris_to_milvus(kc))

        # Assemble TableInfoState
        table_infos: list[TableInfoState] = []
        for tid, cols in table_to_cols.items():
            t = await meta_repo.get_table_info_by_id(tid)
            if not t:
                continue
            table_infos.append(TableInfoState(
                name=t.name, role=t.role, description=t.description or "",
                columns=[_col_milvus_to_state(c) for c in cols],
            ))

        metric_infos = [_metric_milvus_to_state(m) for m in retrieved_metrics]
        logger.info(f"RRF fusion done: {len(table_infos)} tables, {len(metric_infos)} metrics")
        return {"table_infos": table_infos, "metric_infos": metric_infos}
    except Exception as e:
        logger.error(f"RRF fusion error: {e}")
        raise
