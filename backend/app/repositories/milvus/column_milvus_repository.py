
from app.conf.app_config import app_config


class ColumnMilvusRepository:
    collection_name = app_config.milvus.column_collection

    def __init__(self, client):
        self.client = client

    def search_safe(self, embedding: list[float], limit: int = 5):
        """Application-layer vector validation (Milvus client search is synchronous)."""
        if not embedding or len(embedding) != app_config.milvus.embedding_size:
            raise ValueError(
                f"embedding dimension mismatch, expected {app_config.milvus.embedding_size}, "
                f"got {len(embedding) if embedding else 0}"
            )
        results = self.client.search(
            collection_name=self.collection_name,
            data=[embedding],
            limit=limit,
            output_fields=["name", "type", "role", "examples",
                          "description", "alias", "table_id"],
        )
        # Under COSINE, smaller distance means more similar; convert to similarity
        hits = []
        for rank, r in enumerate(results[0]):
            sim = 1 - r.get("distance", 1.0)
            if sim >= 0.25:  # threshold
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
