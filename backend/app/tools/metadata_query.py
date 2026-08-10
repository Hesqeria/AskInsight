"""P4-02: Metadata query API - tables/columns/metrics/glossary/lineage."""
import time


class TableMeta:
    def __init__(self, table_id, name, role="table", layer="", description=""):
        self.id = table_id
        self.name = name
        self.role = role
        self.layer = layer
        self.description = description

    def to_dict(self):
        return {"id": self.id, "name": self.name, "role": self.role,
                "layer": self.layer, "description": self.description}


class ColumnMeta:
    def __init__(self, column_id, table, name, data_type="", comment="",
                 semantic_tags=None, is_metric=False):
        self.id = column_id
        self.table = table
        self.name = name
        self.data_type = data_type
        self.comment = comment
        self.semantic_tags = semantic_tags or []
        self.is_metric = is_metric

    def to_dict(self):
        return {"id": self.id, "table": self.table, "name": self.name,
                "data_type": self.data_type, "comment": self.comment,
                "semantic_tags": self.semantic_tags, "is_metric": self.is_metric}


class MetricInfo:
    def __init__(self, metric_id, name, expression, aliases=None, agg_type="SUM"):
        self.id = metric_id
        self.name = name
        self.expression = expression
        self.aliases = aliases or []
        self.agg_type = agg_type

    def to_dict(self):
        return {"id": self.id, "name": self.name, "expression": self.expression,
                "aliases": self.aliases, "agg_type": self.agg_type}


class GlossaryItem:
    def __init__(self, term, definition, category="business", synonyms=None):
        self.term = term
        self.definition = definition
        self.category = category
        self.synonyms = synonyms or []

    def to_dict(self):
        return {"term": self.term, "definition": self.definition,
                "category": self.category, "synonyms": self.synonyms}


_BUILTIN_TABLES = [
    TableMeta("ods.order", "ods_order", "table", "ods", "order raw layer"),
    TableMeta("ods.payment", "ods_payment", "table", "ods", "payment raw layer"),
    TableMeta("dwd.order_detail", "dwd_order_detail", "table", "dwd", "order detail"),
    TableMeta("dws.gmv_daily", "dws_gmv_daily", "table", "dws", "GMV daily summary"),
    TableMeta("ads.gmv_overview", "ads_gmv_overview", "table", "ads", "GMV overview"),
    TableMeta("dim.date", "dim_date", "table", "dim", "date dimension"),
    TableMeta("dim.user", "dim_user", "table", "dim", "user dimension"),
]

_BUILTIN_COLUMNS = [
    ColumnMeta("dwd_order_detail.payment_amount", "dwd_order_detail",
               "payment_amount", "DECIMAL(18,2)", "payment amount",
               ["user_spend", "spend", "payment_amount"], is_metric=True),
    ColumnMeta("dwd_order_detail.order_id", "dwd_order_detail", "order_id", "STRING", "order id"),
    ColumnMeta("dws_gmv_daily.gmv", "dws_gmv_daily", "gmv", "DECIMAL(18,2)",
               "GMV", ["gmv", "sales", "turnover"], is_metric=True),
    ColumnMeta("dws_gmv_daily.order_cnt", "dws_gmv_daily", "order_cnt", "BIGINT",
               "order count", ["orders", "order_cnt"], is_metric=True),
]

_BUILTIN_METRICS = [
    MetricInfo("metric_gmv", "GMV", "SUM(gmv)", ["gmv", "sales", "turnover"], "SUM"),
    MetricInfo("metric_order_cnt", "order_cnt", "COUNT(DISTINCT order_id)",
               ["orders", "order_cnt"], "COUNT"),
    MetricInfo("metric_avg_order", "avg_order_value", "SUM(gmv)/COUNT(DISTINCT order_id)",
               ["average_order_value"], "DIV"),
]

_BUILTIN_GLOSSARY = [
    GlossaryItem("GMV", "Gross Merchandise Volume", "business", ["gmv", "sales"]),
    GlossaryItem("客单价", "average order value", "business", ["AOV"]),
]

_LAYER_ROLE = {
    "ods": ["L1_business", "L2_analyst", "L3_engineer", "L4_admin"],
    "dwd": ["L1_business", "L2_analyst", "L3_engineer", "L4_admin"],
    "dws": ["L2_analyst", "L3_engineer", "L4_admin"],
    "ads": ["L2_analyst", "L3_engineer", "L4_admin"],
    "dim": ["L1_business", "L2_analyst", "L3_engineer", "L4_admin"],
}


class MetadataQueryAPI:
    def __init__(self, doris=None, milvus=None, cache=None):
        self.doris = doris
        self.milvus = milvus
        self.cache = cache
        self._cache_store = {}

    def _cache_get(self, key, ttl):
        if self.cache is not None:
            return self.cache.get(key)
        entry = self._cache_store.get(key)
        if entry and time.time() - entry[1] < ttl:
            return entry[0]
        return None

    def _cache_set(self, key, value):
        if self.cache is not None:
            self.cache.set(key, value, ex=3600)
        else:
            self._cache_store[key] = (value, time.time())

    def get_table(self, table_id):
        key = f"meta:table:{table_id}"
        cached = self._cache_get(key, 3600)
        if cached:
            return cached
        for t in _BUILTIN_TABLES:
            if t.id == table_id or t.name == table_id:
                self._cache_set(key, t)
                return t
        return None

    def list_tables(self, role=None, layer=None) -> list:
        result = []
        for t in _BUILTIN_TABLES:
            if layer and t.layer != layer:
                continue
            if role and role != "L4_admin":
                if role not in _LAYER_ROLE.get(t.layer, []):
                    continue
            result.append(t.to_dict())
        return result

    def get_column(self, column_id) -> ColumnMeta:
        for c in _BUILTIN_COLUMNS:
            if c.id == column_id or c.name == column_id:
                return c
        return None

    def search_columns_by_semantic(self, query, top_k=10):
        if self.milvus is not None:
            try:
                return self.milvus.search_columns(query, top_k=top_k)
            except Exception:
                pass
        results = []
        q = query.lower()
        for c in _BUILTIN_COLUMNS:
            score = 0
            for tag in c.semantic_tags:
                if q in tag.lower():
                    score = max(score, 1.0)
            if c.is_metric and q in c.name.lower():
                score = max(score, 0.8)
            if score:
                results.append({**c.to_dict(), "score": score})
        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def get_metric(self, metric_id) -> MetricInfo:
        for m in _BUILTIN_METRICS:
            if m.id == metric_id or m.name == metric_id:
                return m
        return None

    def search_metrics_by_alias(self, alias) -> list:
        q = alias.lower()
        return [m.to_dict() for m in _BUILTIN_METRICS
                if q in m.name.lower() or any(q in a.lower() for a in m.aliases)]

    def get_glossary(self, term) -> GlossaryItem:
        q = term.lower()
        for g in _BUILTIN_GLOSSARY:
            if q == g.term.lower() or any(q == s.lower() for s in g.synonyms):
                return g
        return None

    def get_lineage(self, table, direction="upstream", depth=2) -> dict:
        if self.doris is not None:
            try:
                rows = self.doris.query(
                    "SELECT source_table, target_table FROM data_agent.sql_lineage "
                    "WHERE target_table = %s LIMIT 20", params=(table,))
                return {"table": table, "direction": direction,
                        "nodes": [r[0] for r in rows], "depth": depth}
            except Exception:
                pass
        fallback = {
            "dwd_order_detail": {"upstream": ["ods.order", "ods.payment"],
                                 "downstream": ["dws_gmv_daily"]},
            "dws_gmv_daily": {"upstream": ["dwd_order_detail"],
                              "downstream": ["ads_gmv_overview"]},
        }
        norm = table.replace(".", "_") if "." in table else table
        rel = fallback.get(table, fallback.get(norm, {}))
        nodes = rel.get(direction, [])[:depth]
        return {"table": table, "direction": direction, "nodes": nodes, "depth": depth}

    def get_relations(self, entity, depth=1) -> dict:
        if self.doris is not None:
            try:
                rows = self.doris.query(
                    "SELECT source_entity, target_entity, relation_type "
                    "FROM data_agent.entity_relation WHERE source_entity = %s LIMIT 20",
                    params=(entity,))
                return {"entity": entity,
                        "relations": [{"target": r[1], "type": r[2]} for r in rows]}
            except Exception:
                pass
        relations = {
            "user": [{"target": "order", "type": "buy"}, {"target": "payment", "type": "pay"}],
            "order": [{"target": "product", "type": "contains"}],
        }
        return {"entity": entity, "relations": relations.get(entity, [])[:depth * 10]}


_metadata_api = None


def get_metadata_api() -> MetadataQueryAPI:
    global _metadata_api
    if _metadata_api is None:
        _metadata_api = MetadataQueryAPI()
    return _metadata_api
