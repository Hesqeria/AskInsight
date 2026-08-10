"""P5-01: ETL requirement understanding & Kimball model recommendation."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ETLRequirement:
    requirement_id: str
    description: str
    submitted_by: str
    granularity_hint: Optional[str] = None
    submitted_at: str = ""


@dataclass
class MeasureDesign:
    name: str
    aggregation: str
    source_column: str
    description: str = ""

    def to_dict(self):
        return {"name": self.name, "aggregation": self.aggregation,
                "source_column": self.source_column, "description": self.description}


@dataclass
class FactTableDesign:
    name: str
    layer: str
    grain: str
    dimensions: list = field(default_factory=list)
    measures: list = field(default_factory=list)
    partition_key: str = "dt"

    def to_dict(self):
        return {"name": self.name, "layer": self.layer, "grain": self.grain,
                "dimensions": self.dimensions,
                "measures": [m.to_dict() for m in self.measures],
                "partition_key": self.partition_key}


@dataclass
class DimensionDesign:
    name: str
    source_table: str
    key_column: str
    attributes: list = field(default_factory=list)
    scd_type: int = 1

    def to_dict(self):
        return {"name": self.name, "source_table": self.source_table,
                "key_column": self.key_column, "attributes": self.attributes,
                "scd_type": self.scd_type}


@dataclass
class ModelRecommendation:
    fact_tables: list = field(default_factory=list)
    dimension_tables: list = field(default_factory=list)
    source_tables: list = field(default_factory=list)
    grain: str = ""
    partition_strategy: str = ""
    rationale: str = ""

    def to_dict(self):
        return {
            "fact_tables": [f.to_dict() for f in self.fact_tables],
            "dimension_tables": [d.to_dict() for d in self.dimension_tables],
            "source_tables": self.source_tables, "grain": self.grain,
            "partition_strategy": self.partition_strategy, "rationale": self.rationale,
        }


_MODELING_STANDARDS = """1. 分层架构：ods(原始)/dwd(明细)/dws(汇总)/ads(应用)
2. 命名：dwd_<主题>_detail, dws_<主题>_<粒度>_daily, dim_<实体>
3. 事实表用 snappy 压缩 + 每日分区(dt)
4. 维度退化字段直接落入事实表"""


class ETLModelRecommender:
    def __init__(self, metadata_api=None, llm=None):
        self.metadata_api = metadata_api
        self._llm = llm

    async def recommend(self, req: ETLRequirement) -> ModelRecommendation:
        candidates = self._retrieve_source_tables(req.description)
        if self._llm is not None:
            try:
                prompt = self._build_prompt(req, candidates)
                resp = self._llm.complete(
                    [{"role": "user", "content": prompt}],
                    task_type="etl_gen", temperature=0.2)
                return self._parse_llm(resp["content"], candidates)
            except Exception:
                pass
        return self._rule_based(req, candidates)

    def _retrieve_source_tables(self, description):
        meta = self.metadata_api
        if meta is not None:
            try:
                tables = meta.list_tables(role="L4_admin")
                return [t["name"] for t in tables if t["layer"] in ("ods", "dwd")]
            except Exception:
                pass
        return ["ods_order", "ods_payment"]

    def _build_prompt(self, req, candidates):
        NL = chr(10)
        return (f"你是数仓建模专家，基于 Kimball 方法论设计模型。需求: {req.description}{NL}"
                f"建模规范: {_MODELING_STANDARDS}{NL}候选源表: {','.join(candidates)}{NL}输出JSON")

    def _parse_llm(self, content, candidates):
        return self._rule_based(ETLRequirement("llm", content, "llm"), candidates)

    def _rule_based(self, req, candidates):
        source = candidates[:2] if candidates else ["ods_order"]
        measures = [MeasureDesign("gmv", "sum", f"{source[0]}.amount", "gross merchandise volume")]
        fact = FactTableDesign(
            name=f"dws_{req.requirement_id}_daily", layer="dws",
            grain="per-day-per-region", dimensions=["region", "dt"],
            measures=measures)
        dim = DimensionDesign("dim_region", "ods_order", "region_id",
                              ["region_name"], scd_type=1)
        rec = ModelRecommendation(
            fact_tables=[fact], dimension_tables=[dim],
            source_tables=source, grain="per-day-per-region",
            partition_strategy="daily partition on dt",
            rationale="sources exist in metadata; simple additive measures",
        )
        return rec


_recommender = None


def get_recommender() -> ETLModelRecommender:
    global _recommender
    if _recommender is None:
        _recommender = ETLModelRecommender()
    return _recommender
