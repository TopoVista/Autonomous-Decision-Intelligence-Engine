"""RAG Retriever: orchestrates parsing, chunking, embedding, and retrieval.

Provides a high-level interface for ingesting documents and retrieving
relevant chunks to ground LLM responses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.memory.embedding_service import EmbeddingService
from app.rag.chunker import Chunk, chunk_text
from app.rag.parser import parse_document
from app.rag.vector_store import (
    InMemoryVectorStore,
    StoredChunk,
    VectorStore,
    get_default_store,
)


@dataclass
class RetrievalResult:
    """A single retrieval result with the chunk and its relevance score."""

    chunk_text: str
    source: str
    chunk_index: int
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_text": self.chunk_text,
            "source": self.source,
            "chunk_index": self.chunk_index,
            "score": self.score,
            "metadata": self.metadata,
        }


class RAGRetriever:
    """High-level RAG retriever with per-user scoping.

    Workflow:
        1. Parse document (PDF/TXT/MD/HTML)
        2. Chunk into overlapping segments
        3. Embed each chunk
        4. Store with user_id for access control
        5. Retrieve top-K relevant chunks for a query
    """

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        embedder: EmbeddingService | None = None,
    ) -> None:
        self.store = vector_store or get_default_store()
        self.embedder = embedder or EmbeddingService()

    async def ingest_document(
        self,
        content: bytes,
        source: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest a document: parse, chunk, embed, and store.

        Args:
            content: Raw document bytes.
            source: Filename or identifier.
            user_id: Owner of the document (for access control).
            metadata: Additional metadata to attach.

        Returns:
            Dict with ingestion stats (num_chunks, source, etc.).
        """
        # Step 1: Parse
        parsed = parse_document(content, source)

        # Step 2: Chunk
        chunks: list[Chunk] = chunk_text(
            text=parsed.text,
            source=source,
            metadata={**parsed.metadata, **(metadata or {})},
        )

        if not chunks:
            return {
                "source": source,
                "num_chunks": 0,
                "status": "empty",
                "message": "No extractable text found in document.",
            }

        settings = get_settings()
        if len(chunks) > settings.max_document_chunks:
            raise ValueError(f"Document creates {len(chunks)} chunks; limit is {settings.max_document_chunks}.")
        if isinstance(self.store, InMemoryVectorStore) and self.store.count() + len(chunks) > settings.max_in_memory_chunks:
            raise MemoryError("Document storage is at capacity. Delete an existing document or configure Chroma.")

        # Step 3: Embed each chunk
        stored_chunks: list[StoredChunk] = []
        for chunk in chunks:
            embedding = await self.embedder.embed_text(chunk.text)
            stored = StoredChunk(
                id=uuid.uuid4().hex,
                text=chunk.text,
                source=chunk.source,
                chunk_index=chunk.chunk_index,
                embedding=embedding,
                metadata=chunk.metadata,
                user_id=user_id,
            )
            stored_chunks.append(stored)

        # Step 4: Store
        await self.store.add_chunks(stored_chunks)

        return {
            "source": source,
            "num_chunks": len(stored_chunks),
            "status": "success",
            "document_type": parsed.metadata.get("type", "unknown"),
            "document_size_bytes": parsed.metadata.get("size_bytes", 0),
        }

    async def retrieve(
        self,
        query: str,
        user_id: str,
        *,
        limit: int = 5,
        source: str | None = None,
    ) -> list[RetrievalResult]:
        """Retrieve relevant chunks for a query, scoped to the user.

        Args:
            query: The search query.
            user_id: User ID for access control (only their docs are searched).
            limit: Maximum number of chunks to return.
            source: Optional filter by document source.

        Returns:
            List of RetrievalResult sorted by relevance (descending).
        """
        # Embed the query
        query_embedding = await self.embedder.embed_text(query)

        # Search the vector store
        results = await self.store.search(
            query_embedding,
            limit=limit,
            user_id=user_id,
            source=source,
        )

        return [
            RetrievalResult(
                chunk_text=chunk.text,
                source=chunk.source,
                chunk_index=chunk.chunk_index,
                score=score,
                metadata=chunk.metadata,
            )
            for chunk, score in results
        ]

    async def delete_user_documents(self, user_id: str) -> int:
        """Delete all documents belonging to a user.

        Returns the number of chunks deleted.
        """
        return await self.store.delete_by_user(user_id)

    async def delete_document(self, source: str, user_id: str) -> int:
        """Delete all chunks from a specific document source.

        Returns the number of chunks deleted.
        """
        return await self.store.delete_by_source(source, user_id=user_id)
