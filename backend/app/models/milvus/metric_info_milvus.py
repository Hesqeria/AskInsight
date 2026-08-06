from typing import TypedDict


class MetricInfoMilvus(TypedDict):
    id: str
    name: str
    description: str
    relevant_columns: list
    alias: list
