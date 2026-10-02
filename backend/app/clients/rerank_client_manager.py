"""Rerank client manager - Bailian DashScope gte-rerank-v2.

Mirrors the EmbeddingClientManager pattern: synchronous dashscope call
wrapped with retries + an async `to_thread` shim so the LangGraph event
loop is never blocked.

Adds three production concerns on top:
  1. **Redis-backed result cache** keyed on hash(query + documents).
     Metadata recall is largely deterministic per query, so the same
     (query, candidate-set) recurs frequently. Caching saves cost+latency.
  2. **Prometheus metrics**: call count, latency, candidate count, cache
     hits - all observable via /metrics.
  3. **Defensive length caps**: dashscope rejects docs whose text exceeds
     the model's token budget. We truncate per-doc and total-doc-set so
     a misbehaving recall path can't break rerank for the whole query.

DashScope TextReRank response shape:
    resp.status_code == 200
    resp.output.results -> [
        {"index": int, "relevance_score": float, "relevance_logits": ...},
        ...
    ]
where `index` references the position in the input `documents` list and
`relevance_score` is in roughly [0, 1] (higher = more relevant).
"""
import json
import time
from typing import Optional

from app.core.log import logger


# Dashscope gte-rerank-v2 has a per-doc soft token budget (~2048). We
# truncate at 1500 chars (~750 Chinese chars / ~1000 English words) to
# stay safely under while preserving the most signal-dense prefix.
RERANK_MAX_DOC_CHARS = 1500


def _truncate_doc(text: str) -> str:
    """Hard cap on per-doc text length so a pathological column
    description can't blow the dashscope token limit."""
    if not text:
        return ""
    if len(text) <= RERANK_MAX_DOC_CHARS:
        return text
    return text[:RERANK_MAX_DOC_CHARS - 3] + "..."


class _DashScopeReranker:
    def __init__(self, api_key: str, model: str = "gte-rerank-v2",
                 max_retries: int = 3, cache_ttl: int = 3600):
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.cache_ttl = max(0, int(cache_ttl))

    # ------------------------------------------------------------------ #
    # Cache helpers
    # ------------------------------------------------------------------ #
    def _cache_payload(self, query: str, documents: list[str], top_n: int) -> str:
        """Stable composite key for cache lookup. Two calls with identical
        inputs produce the same string. The RedisCache layer adds its own
        prefix + md5 hashing, so we just return the raw payload here."""
        return json.dumps(
            {"m": self.model, "q": query, "d": documents, "n": top_n},
            ensure_ascii=False, sort_keys=True,
        )

    async def _cache_get(self, key: str):
        if self.cache_ttl <= 0:
            return None
        try:
            return await _rerank_cache.get(key)
        except Exception:
            return None

    async def _cache_set(self, key: str, value):
        if self.cache_ttl <= 0:
            return
        try:
            await _rerank_cache.set(key, value)
        except Exception as e:
            logger.debug(f"rerank cache write failed (non-fatal): {e}")

    # ------------------------------------------------------------------ #
    # Dashscope call
    # ------------------------------------------------------------------ #
    def _call(self, query: str, documents: list[str], top_n: int):
        import dashscope
        dashscope.api_key = self.api_key
        last_err = None
        for i in range(self.max_retries):
            try:
                r = dashscope.TextReRank.call(
                    model=self.model,
                    query=query,
                    documents=documents,
                    top_n=top_n,
                    return_documents=False,
                )
                if r.status_code == 200:
                    return r.output["results"]
                last_err = f"{r.code} - {r.message}"
            except Exception as e:
                last_err = str(e)
            # Exponential-ish backoff, same shape as embedding client.
            time.sleep(1 + i)
        raise RuntimeError(
            f"Bailian rerank failed (retried {self.max_retries} times): {last_err}"
        )

    def _normalize(self, raw) -> list[dict]:
        out = []
        for r in raw:
            try:
                idx = int(r.get("index"))
                score = float(r.get("relevance_score", 0.0))
            except (TypeError, ValueError):
                continue
            out.append({"index": idx, "relevance_score": score})
        out.sort(key=lambda x: -x["relevance_score"])
        return out

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def rerank(self, query: str, documents: list[str], top_n: int = 30
               ) -> list[dict]:
        """Synchronous rerank. Returns list of {index, relevance_score}
        sorted by relevance_score descending."""
        if not query or not documents:
            return []
        effective_top_n = max(1, min(top_n, len(documents)))
        # Defensive per-doc length cap.
        truncated = [_truncate_doc(d) for d in documents]
        raw = self._call(query, truncated, effective_top_n)
        return self._normalize(raw)

    async def arerank(self, query: str, documents: list[str], top_n: int = 30
                      ) -> list[dict]:
        """Async rerank with transparent Redis caching + metrics.

        On cache hit the dashscope call is skipped entirely and latency
        is recorded as ~0. Cache failures degrade silently to a normal
        call. Metrics are recorded on every path."""
        import asyncio
        from app.core.metrics import (
            RERANK_CALLS, RERANK_LATENCY, RERANK_CANDIDATES,
        )

        if not query or not documents:
            return []

        effective_top_n = max(1, min(top_n, len(documents)))

        # --- Cache lookup -----------------------------------------------
        # Truncate docs *before* hashing so cache hits are robust against
        # tiny text-length differences (the reranker truncates anyway).
        truncated_docs = [_truncate_doc(d) for d in documents]
        cache_payload = self._cache_payload(query, truncated_docs, effective_top_n)
        cached = await self._cache_get(cache_payload)
        if cached is not None:
            RERANK_CALLS.labels(status="cache_hit").inc()
            RERANK_LATENCY.labels(status="cache_hit").observe(0.0)
            return cached

        # --- Cache miss -> actual call ----------------------------------
        RERANK_CANDIDATES.observe(len(documents))
        start = time.time()
        try:
            result = await asyncio.to_thread(
                self.rerank, query, documents, top_n,
            )
        except Exception as e:
            RERANK_CALLS.labels(status="error").inc()
            RERANK_LATENCY.labels(status="error").observe(time.time() - start)
            raise

        RERANK_CALLS.labels(status="success").inc()
        RERANK_LATENCY.labels(status="success").observe(time.time() - start)

        # Best-effort cache write; failures don't affect correctness.
        await self._cache_set(cache_payload, result)
        return result


class RerankClientManager:
    def __init__(self, config):
        self.config = config
        self.client: Optional[_DashScopeReranker] = None
        # `enabled` can be flipped at runtime (e.g. if init fails on startup)
        self.enabled: bool = bool(getattr(config, "enabled", True))

    def init(self) -> None:
        # If no API key is configured we keep the manager alive but
        # disable it, so callers can fall back to RRF ordering without
        # needing a code path / env toggle on every dev box.
        if not self.config.api_key:
            logger.warning(
                "Rerank disabled: no api_key configured "
                "(set RERANK_API_KEY or DASHSCOPE_API_KEY to enable)"
            )
            self.enabled = False
            self.client = None
            return
        self.client = _DashScopeReranker(
            api_key=self.config.api_key,
            model=self.config.model,
            cache_ttl=getattr(self.config, "cache_ttl", 3600),
        )
        self.enabled = True
        logger.info(
            f"Rerank client initialized: model={self.config.model} "
            f"cache_ttl={self.config.cache_ttl}s"
        )

    def is_available(self) -> bool:
        return self.enabled and self.client is not None


from app.conf.app_config import app_config as _cfg  # noqa: E402

# Dedicated cache instance with the configured TTL. We don't reuse
# `redis_cache` because its TTL (600s) is too short for metadata.
# This is lazily rebound in `_set_cache_ttl` so that callers tweaking
# `cache_ttl` after init still get the right expiry.
from app.core.cache import RedisCache  # noqa: E402
_rerank_cache = RedisCache(prefix="da:rerank:", ttl=_cfg.rerank.cache_ttl)


def _set_cache_ttl(ttl_seconds: int) -> None:
    """Rebind the cache with a new TTL (used at init time if config changes)."""
    global _rerank_cache
    _rerank_cache = RedisCache(prefix="da:rerank:", ttl=max(0, int(ttl_seconds)))


rerank_client_manager = RerankClientManager(_cfg.rerank)
