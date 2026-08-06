from typing import TypedDict


class ColumnInfoMilvus(TypedDict):
    id: str
    name: str
    type: str
    role: str
    examples: list
    description: str
    alias: list
    table_id: str
