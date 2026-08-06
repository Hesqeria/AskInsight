from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ColumnConfig:
    name: str
    role: str
    description: str
    alias: list[str] = field(default_factory=list)
    sync: bool = False


@dataclass
class TableConfig:
    name: str
    role: str
    description: str
    columns: list[ColumnConfig] = field(default_factory=list)


@dataclass
class MetricConfig:
    name: str
    description: str
    relevant_columns: list[str] = field(default_factory=list)
    alias: list[str] = field(default_factory=list)


@dataclass
class MetaConfig:
    tables: Optional[list[TableConfig]] = None
    metrics: Optional[list[MetricConfig]] = None
