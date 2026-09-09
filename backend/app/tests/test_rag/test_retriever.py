"""Tests for RAG retriever."""

from __future__ import annotations

import pytest

from app.rag.retriever import RAGRetriever, RetrievalResult
from app.rag.vector_store import InMemoryVectorStore


class TestRAGRetriever:
    @pytest.mark.asyncio
    async def test_ingest_and_retrieve(self):
        store = InMemoryVectorStore()
        retriever = RAGRetriever(vector_store=store)

        # Ingest a document
        doc_content = b"The capital of France is Paris. The capital of Germany is Berlin. The capital of Spain is Madrid."
        result = await retriever.ingest_document(
            content=doc_content,
            source="capitals.txt",
            user_id="user-1",
        )
        assert result["status"] == "success"
        assert result["num_chunks"] >= 1

        # Retrieve relevant chunks
        results = await retriever.retrieve(
            query="What is the capital of France?",
            user_id="user-1",
            limit=3,
        )
        assert len(results) >= 1
        assert isinstance(results[0], RetrievalResult)
        assert results[0].source == "capitals.txt"

    @pytest.mark.asyncio
    async def test_retrieve_scoped_by_user(self):
        store = InMemoryVectorStore()
        retriever = RAGRetriever(vector_store=store)

        # Ingest for user-1
        await retriever.ingest_document(
            content=b"User1 private document content.",
            source="private.txt",
            user_id="user-1",
        )
        # Ingest for user-2
        await retriever.ingest_document(
            content=b"User2 private document content.",
            source="private2.txt",
            user_id="user-2",
        )

        # User-1 should only see their own docs
        results = await retriever.retrieve(
            query="private document",
            user_id="user-1",
            limit=5,
        )
        assert all(r.source != "private2.txt" for r in results)

    @pytest.mark.asyncio
    async def test_ingest_empty_document(self):
        store = InMemoryVectorStore()
        retriever = RAGRetriever(vector_store=store)

        result = await retriever.ingest_document(
            content=b"",
            source="empty.txt",
            user_id="user-1",
        )
        assert result["status"] == "empty"

    @pytest.mark.asyncio
    async def test_delete_user_documents(self):
        store = InMemoryVectorStore()
        retriever = RAGRetriever(vector_store=store)

        await retriever.ingest_document(
            content=b"Document to delete.",
            source="delete_me.txt",
            user_id="user-1",
        )
        assert store.count() >= 1

        deleted = await retriever.delete_user_documents("user-1")
        assert deleted >= 1
        assert store.count() == 0

    @pytest.mark.asyncio
    async def test_delete_specific_document(self):
        store = InMemoryVectorStore()
        retriever = RAGRetriever(vector_store=store)

        await retriever.ingest_document(
            content=b"Document A content.",
            source="doc_a.txt",
            user_id="user-1",
        )
        await retriever.ingest_document(
            content=b"Document B content.",
            source="doc_b.txt",
            user_id="user-1",
        )

        deleted = await retriever.delete_document("doc_a.txt", "user-1")
        assert deleted >= 1

    @pytest.mark.asyncio
    async def test_delete_document_is_scoped_to_its_owner(self):
        store = InMemoryVectorStore()
        retriever = RAGRetriever(vector_store=store)
        await retriever.ingest_document(b"Owner one document.", "shared.txt", "user-1")
        await retriever.ingest_document(b"Owner two document.", "shared.txt", "user-2")

        deleted = await retriever.delete_document("shared.txt", "user-1")
        assert deleted >= 1
        remaining = await retriever.retrieve("owner two", "user-2")
        assert remaining

    @pytest.mark.asyncio
    async def test_retrieve_with_source_filter(self):
        store = InMemoryVectorStore()
        retriever = RAGRetriever(vector_store=store)

        await retriever.ingest_document(
            content=b"Report about quarterly sales.",
            source="sales_q1.txt",
            user_id="user-1",
        )
        await retriever.ingest_document(
            content=b"Report about annual budget.",
            source="budget_2024.txt",
            user_id="user-1",
        )

        results = await retriever.retrieve(
            query="sales figures",
            user_id="user-1",
            source="sales_q1.txt",
        )
        assert all(r.source == "sales_q1.txt" for r in results)

    @pytest.mark.asyncio
    async def test_result_to_dict(self):
        result = RetrievalResult(
            chunk_text="test text",
            source="test.txt",
            chunk_index=0,
            score=0.95,
            metadata={"type": "text"},
        )
        d = result.to_dict()
        assert d["chunk_text"] == "test text"
        assert d["source"] == "test.txt"
        assert d["score"] == 0.95
