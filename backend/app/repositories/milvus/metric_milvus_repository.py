from app.conf.app_config import app_config


class MetricMilvusRepository:
    collection_name = app_config.milvus.metric_collection

    def __init__(self, client):
        self.client = client

    def search_safe(self, embedding: list[float], limit: int = 5):
        if not embedding or len(embedding) != app_config.milvus.embedding_size:
            raise ValueError("embedding dimension mismatch")
        results = self.client.search(
            collection_name=self.collection_name,
            data=[embedding],
            limit=limit,
            output_fields=["name", "description", "relevant_columns", "alias"],
        )
        hits = []
        for rank, r in enumerate(results[0]):
            sim = 1 - r.get("distance", 1.0)
            if sim >= 0.25:
                entity = r.get("entity", {})
                entity["id"] = r.get("id")
                entity["_rank"] = rank
                entity["_similarity"] = round(sim, 4)
                hits.append(entity)
        return hits


    async def async_search_safe(self, embedding: list[float], limit: int = 5):
        """C2: async wrapper around Milvus search (avoids blocking the event loop)."""
        import asyncio
        return await asyncio.to_thread(self.search_safe, embedding, limit)
