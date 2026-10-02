from typing import TypedDict


class DataAgentContext(TypedDict, total=False):
    embedding_client: object
    rerank_client: object  # optional; when absent, merge node uses pure RRF
    column_milvus_repository: object
    metric_milvus_repository: object
    value_doris_repository: object
    meta_doris_repository: object
    dw_doris_repository: object
    # Optional. When present, decision_insight uses Thompson Sampling
    # to pick a prompt policy and logs the choice for later reward
    # attribution. When absent, the node falls back to the default
    # `facts_first` policy deterministically.
    rl_repository: object
    # Optional shared sampler (mostly for tests to pin the RNG seed).
    # Production leaves this unset so the module-level singleton is used.
    rl_sampler: object
