"""P5-02: ETL SQL generation - create DDL + insert + transformations."""
from dataclasses import dataclass, field


@dataclass
class Transform:
    target_column: str
    expression: str
    source_columns: list = field(default_factory=list)
    transform_type: str = "direct"
    description: str = ""

    def to_dict(self):
        return {"target_column": self.target_column, "expression": self.expression,
                "source_columns": self.source_columns,
                "transform_type": self.transform_type, "description": self.description}


@dataclass
class ETLSQLPackage:
    table_name: str
    create_ddl: str
    insert_sql: str
    transformations: list = field(default_factory=list)
    comments: dict = field(default_factory=dict)
    coding_standards_applied: list = field(default_factory=list)

    def to_dict(self):
        return {"table_name": self.table_name, "create_ddl": self.create_ddl,
                "insert_sql": self.insert_sql,
                "transformations": [t.to_dict() for t in self.transformations],
                "comments": self.comments,
                "coding_standards_applied": self.coding_standards_applied}


class ETLSQLGenerator:
    def __init__(self, llm=None, metadata_api=None):
        self._llm = llm
        self.metadata_api = metadata_api

    async def generate(self, model) -> ETLSQLPackage:
        fact = model.fact_tables[0] if model.fact_tables else None
        table_name = fact.name if fact else "dws_etl_target_daily"
        source = model.source_tables[0] if model.source_tables else "ods_order"
        return self._build_package(table_name, source, fact)

    def _build_package(self, table_name, source, fact) -> ETLSQLPackage:
        create_ddl = self._create_ddl(table_name)
        insert_sql = self._insert_sql(table_name, source)
        transforms = self._default_transforms(source)
        comments = {"dt": "data date partition", "gmv": "gross merchandise volume"}
        return ETLSQLPackage(
            table_name=table_name, create_ddl=create_ddl, insert_sql=insert_sql,
            transformations=transforms, comments=comments,
            coding_standards_applied=["命名规范", "分区裁剪", "注释规范"])

    @staticmethod
    def _create_ddl(table_name):
        return """CREATE TABLE IF NOT EXISTS %s (
  dt DATE NOT NULL COMMENT 'data date',
  region STRING COMMENT 'region',
  gmv DECIMAL(18,2) COMMENT 'gmv'
) ENGINE=OLAP
DUPLICATE KEY(dt)
PARTITION BY RANGE(dt)(
  PARTITION p2024 VALUES LESS THAN ('2024-01-01')
)
DISTRIBUTED BY HASH(dt) BUCKETS 10
PROPERTIES ("replication_num" = "1");""" % table_name

    @staticmethod
    def _insert_sql(table_name, source):
        return """INSERT OVERWRITE TABLE %s PARTITION(dt)
SELECT
  dt,
  region,
  SUM(amount) AS gmv
FROM %s
WHERE dt = '${business_date}'
GROUP BY dt, region;""" % (table_name, source)

    @staticmethod
    def _default_transforms(source):
        return [
            Transform("gmv", "SUM(" + source + ".amount)", ["amount"], "compute",
                      "sum amount as gmv"),
            Transform("dt", "DATE_FORMAT(create_time, 'yyyy-MM-dd')",
                      ["create_time"], "format_date", "format create_time"),
        ]


_generator = None


def get_sql_generator() -> ETLSQLGenerator:
    global _generator
    if _generator is None:
        _generator = ETLSQLGenerator()
    return _generator
