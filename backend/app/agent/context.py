from typing import TypedDict


class DataAgentContext(TypedDict, total=False):
    embedding_client: object
    column_milvus_repository: object
    metric_milvus_repository: object
    value_doris_repository: object
    meta_doris_repository: object
    dw_doris_repository: object
