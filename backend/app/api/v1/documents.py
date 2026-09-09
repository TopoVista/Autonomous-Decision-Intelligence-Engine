"""API endpoints for document upload and RAG retrieval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.config import get_settings
from app.dependencies import get_current_user, get_db
from app.rag.retriever import RAGRetriever
from app.rag.vector_store import get_default_store
from app.schemas.auth import AuthenticatedUser
from app.services.user_service import ensure_user

router = APIRouter(prefix="/documents", tags=["documents"])


async def _read_upload_with_limit(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while chunk := await file.read(64 * 1024):
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(status_code=413, detail=f"Upload exceeds the {max_bytes} byte limit.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Upload a document for RAG indexing.

    Supports PDF, TXT, Markdown, and HTML files.
    The document is parsed, chunked, embedded, and stored with per-user scoping.
    """
    user = await ensure_user(db, current_user)

    # Validate file type
    filename = file.filename or "unknown"
    supported = {".txt", ".text", ".md", ".markdown", ".html", ".htm", ".pdf"}
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in supported:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(supported))}",
        )

    # Read file content
    content = await _read_upload_with_limit(file, get_settings().max_upload_bytes)
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Ingest document
    retriever = RAGRetriever(vector_store=get_default_store())
    try:
        result = await retriever.ingest_document(content=content, source=filename, user_id=str(user.id))
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MemoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return result


@router.post("/search")
async def search_documents(
    query: str = Query(min_length=1, max_length=1000),
    limit: int = Query(default=5, ge=1, le=20),
    source: str | None = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Search uploaded documents for relevant chunks.

    Returns the top-K most relevant chunks from the user's documents,
    with source citations and relevance scores.
    """
    user = await ensure_user(db, current_user)

    retriever = RAGRetriever(vector_store=get_default_store())
    results = await retriever.retrieve(
        query=query,
        user_id=str(user.id),
        limit=limit,
        source=source,
    )

    return {
        "query": query,
        "results": [r.to_dict() for r in results],
        "count": len(results),
    }


@router.delete("/{source:path}")
async def delete_document(
    source: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Delete a document and all its indexed chunks."""
    user = await ensure_user(db, current_user)

    retriever = RAGRetriever(vector_store=get_default_store())
    deleted = await retriever.delete_document(source, str(user.id))

    return {"source": source, "chunks_deleted": deleted}
