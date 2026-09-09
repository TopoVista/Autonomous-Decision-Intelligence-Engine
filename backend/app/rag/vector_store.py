"""Vector store abstraction for RAG.

Provides a unified interface for storing and retrieving document chunk embeddings.
Supports Chroma (when available) with an in-process fallback for local development.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoredChunk:
    """A chunk stored in the vector store with its embedding."""

    id: str
    text: str
    source: str
    chunk_index: int
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    user_id: str | None = None


class VectorStore:
    """Abstract base for vector stores."""

    async def add_chunks(self, chunks: list[StoredChunk]) -> None:
        """Add chunks with their embeddings to the store."""
        raise NotImplementedError

    async def search(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        user_id: str | None = None,
        source: str | None = None,
    ) -> list[tuple[StoredChunk, float]]:
        """Search for chunks similar to the query embedding."""
        raise NotImplementedError

    async def delete_by_user(self, user_id: str) -> int:
        """Delete all chunks belonging to a user."""
        raise NotImplementedError

    async def delete_by_source(self, source: str, *, user_id: str | None = None) -> int:
        """Delete all chunks from a source."""
        raise NotImplementedError

    def count(self) -> int:
        """Return total number of chunks."""
        raise NotImplementedError


class InMemoryVectorStore(VectorStore):
    """In-process vector store using cosine similarity.

    Suitable for local development and testing. Not persistent across restarts.
    """

    def __init__(self) -> None:
        self._items: dict[str, StoredChunk] = {}

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        length = min(len(a), len(b))
        dot = sum(a[i] * b[i] for i in range(length))
        norm_a = math.sqrt(sum(v * v for v in a[:length]))
        norm_b = math.sqrt(sum(v * v for v in b[:length]))
        if not norm_a or not norm_b:
            return 0.0
        return dot / (norm_a * norm_b)

    async def add_chunks(self, chunks: list[StoredChunk]) -> None:
        for chunk in chunks:
            self._items[chunk.id] = chunk

    async def search(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        user_id: str | None = None,
        source: str | None = None,
    ) -> list[tuple[StoredChunk, float]]:
        results: list[tuple[StoredChunk, float]] = []

        for item in self._items.values():
            if user_id is not None and item.user_id != user_id:
                continue
            if source is not None and item.source != source:
                continue

            score = self._cosine_similarity(query_embedding, item.embedding)
            if score > 0:
                results.append((item, round(score, 4)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    async def delete_by_user(self, user_id: str) -> int:
        to_delete = [k for k, v in self._items.items() if v.user_id == user_id]
        for k in to_delete:
            del self._items[k]
        return len(to_delete)

    async def delete_by_source(self, source: str, *, user_id: str | None = None) -> int:
        to_delete = [
            key for key, value in self._items.items()
            if value.source == source and (user_id is None or value.user_id == user_id)
        ]
        for k in to_delete:
            del self._items[k]
        return len(to_delete)

    def count(self) -> int:
        return len(self._items)


class ChromaVectorStore(VectorStore):
    """Chroma-backed vector store for production use.

    Requires a running Chroma server (CHROMA_HOST env var).
    """

    def __init__(self, host: str, port: int = 8000) -> None:
        self.host = host
        self.port = port
        self._collection = None

    async def _get_collection(self):
        if self._collection is not None:
            return self._collection

        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            client = chromadb.HttpClient(
                host=self.host,
                port=self.port,
                settings=ChromaSettings(allow_reset=True),
            )
            self._collection = client.get_or_create_collection(
                name="rag_documents",
                metadata={"hnsw:space": "cosine"},
            )
        except ImportError:
            raise ImportError(
                "chromadb is required for Chroma vector store. "
                "Install it with: pip install chromadb"
            )
        return self._collection

    async def add_chunks(self, chunks: list[StoredChunk]) -> None:
        if not chunks:
            return

        collection = await self._get_collection()
        ids = [c.id for c in chunks]
        documents = [c.text for c in chunks]
        embeddings = [c.embedding for c in chunks]
        metadatas = [
            {
                "source": c.source,
                "chunk_index": c.chunk_index,
                "user_id": c.user_id or "",
                **{k: str(v) for k, v in c.metadata.items()},
            }
            for c in chunks
        ]

        collection.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    async def search(
        self,
        query_embedding: list[float],
        *,
        limit: int = 5,
        user_id: str | None = None,
        source: str | None = None,
    ) -> list[tuple[StoredChunk, float]]:
        collection = await self._get_collection()

        where: dict[str, Any] = {}
        if user_id is not None:
            where["user_id"] = user_id
        if source is not None:
            where["source"] = source

        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": limit,
        }
        if where:
            kwargs["where"] = where

        results = collection.query(**kwargs)

        chunks_with_scores: list[tuple[StoredChunk, float]] = []
        if not results["ids"] or not results["ids"][0]:
            return chunks_with_scores

        for i, chunk_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i] if results["distances"] else 0.0
            score = 1.0 - distance
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            chunk = StoredChunk(
                id=chunk_id,
                text=results["documents"][0][i] if results["documents"] else "",
                source=metadata.get("source", ""),
                chunk_index=metadata.get("chunk_index", 0),
                embedding=[],
                metadata={k: v for k, v in metadata.items() if k not in ("source", "chunk_index", "user_id")},
                user_id=metadata.get("user_id") or None,
            )
            chunks_with_scores.append((chunk, round(score, 4)))

        return chunks_with_scores

    async def delete_by_user(self, user_id: str) -> int:
        collection = await self._get_collection()
        result = collection.get(where={"user_id": user_id})
        ids = result.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        return len(ids)

    async def delete_by_source(self, source: str, *, user_id: str | None = None) -> int:
        collection = await self._get_collection()
        where: dict[str, str] = {"source": source}
        if user_id is not None:
            where["user_id"] = user_id
        result = collection.get(where=where)
        ids = result.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        return len(ids)

    def count(self) -> int:
        if self._collection is not None:
            return self._collection.count()
        return 0


def get_vector_store() -> VectorStore:
    """Factory: return the configured vector store.

    Uses Chroma when CHROMA_HOST is set, otherwise falls back to in-memory store.
    """
    from app.config import get_settings

    settings = get_settings()
    chroma_host = getattr(settings, "chroma_host", "")
    if chroma_host:
        port = getattr(settings, "chroma_port", 8000)
        return ChromaVectorStore(host=chroma_host, port=port)
    return InMemoryVectorStore()


_default_store: VectorStore | None = None


def get_default_store() -> VectorStore:
    """Get or create the default vector store singleton."""
    global _default_store
    if _default_store is None:
        _default_store = get_vector_store()
    return _default_store
