"""Embedding client manager - Bailian DashScope text-embedding-v3 (1024-dim)."""
import time
from typing import Optional


class _DashScopeEmbeddings:
    def __init__(self, api_key: str, model: str = "text-embedding-v3",
                 max_retries: int = 3):
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries

    def _call(self, texts):
        import dashscope
        dashscope.api_key = self.api_key
        last_err = None
        for i in range(self.max_retries):
            try:
                r = dashscope.TextEmbedding.call(
                    model=self.model, input=texts, dimension=1024,
                )
                if r.status_code == 200:
                    return [e["embedding"] for e in r.output["embeddings"]]
                last_err = f"{r.code} - {r.message}"
            except Exception as e:
                last_err = str(e)
            time.sleep(1 + i)
        raise RuntimeError(f"Bailian embedding failed (retried {self.max_retries} times): {last_err}")

    def embed_query(self, text: str) -> list[float]:
        return self._call([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = []
        for i in range(0, len(texts), 10):
            result.extend(self._call(texts[i:i+10]))
        return result

    async def aembed_query(self, text: str) -> list[float]:
        import asyncio
        return await asyncio.to_thread(self.embed_query, text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        import asyncio
        return await asyncio.to_thread(self.embed_documents, texts)


class EmbeddingClientManager:
    def __init__(self, config):
        self.config = config
        self.client: Optional[_DashScopeEmbeddings] = None

    def init(self) -> None:
        self.client = _DashScopeEmbeddings(
            api_key=self.config.api_key,
            model=self.config.model,
        )


from app.conf.app_config import app_config as _cfg  # noqa: E402
embedding_client_manager = EmbeddingClientManager(_cfg.embedding)
