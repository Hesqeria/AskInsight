"""P5-04: ETL quality rule & alert generation."""
from dataclasses import dataclass, field


@dataclass
class QualityRuleAuto:
    table: str
    column: str = None
    rule_type: str = "row_count_drop"
    threshold: dict = field(default_factory=dict)
    severity: str = "P1"
    alert_channels: list = field(default_factory=list)

    def to_dict(self):
        return {"table": self.table, "column": self.column,
                "rule_type": self.rule_type, "threshold": self.threshold,
                "severity": self.severity, "alert_channels": self.alert_channels}


class ETLQualityGenerator:
    DEFAULT_RULES = [
        {"rule_type": "row_count_drop",
         "threshold": {"compared_to": "yesterday", "max_drop_pct": 50},
         "severity": "P0"},
        {"rule_type": "null_rate",
         "threshold": {"columns": ["primary_key"], "max_rate": 0},
         "severity": "P0"},
        {"rule_type": "null_rate",
         "threshold": {"columns": ["foreign_keys"], "max_rate": 0.05},
         "severity": "P1"},
        {"rule_type": "freshness",
         "threshold": {"max_delay_hours": 4},
         "severity": "P1"},
    ]

    def __init__(self, schema_reader=None):
        self.schema_reader = schema_reader

    async def generate(self, etl_target, schema) -> list:
        rules = []
        for tpl in self.DEFAULT_RULES:
            rules.append(self._instantiate(tpl, etl_target, schema))
        return rules

    def _instantiate(self, tpl, etl_target, schema):
        rule = QualityRuleAuto(
            table=etl_target, rule_type=tpl["rule_type"],
            threshold=tpl["threshold"], severity=tpl["severity"],
            alert_channels=["dingtalk", "email"])
        if tpl["rule_type"] == "null_rate":
            pk = self._primary_key(schema)
            rule.column = pk or None
            if pk:
                rule.threshold = dict(tpl["threshold"])
                rule.threshold["columns"] = [pk]
        return rule

    @staticmethod
    def _primary_key(schema):
        for col in schema or []:
            if col.get("is_primary_key") or col.get("name", "").endswith("_id"):
                return col.get("name")
        return None


_quality_gen = None


def get_quality_generator() -> ETLQualityGenerator:
    global _quality_gen
    if _quality_gen is None:
        _quality_gen = ETLQualityGenerator()
    return _quality_gen
