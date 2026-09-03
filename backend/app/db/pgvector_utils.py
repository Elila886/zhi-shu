import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from langchain.embeddings import init_embeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader
from langchain_community.document_loaders.base import BaseLoader
from langchain_core.documents import Document
from langchain_postgres import PGVector
from loguru import logger

from app.config import settings

embeddings = init_embeddings(
    model=settings.embeddings_model_name,
    base_url=settings.embeddings_base_url,
    provider=settings.model_provider,
    api_key=settings.api_key,
    check_embedding_ctx_length=False,
)


vector_store = PGVector(
    embeddings=embeddings,  # type: ignore
    connection=settings.pgvector_connection,
    collection_name=settings.pgvector_collection_name,
    use_jsonb=True,
    async_mode=True,
)

_vector_store_initialization_lock = asyncio.Lock()


text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
)


DOCUMENT_LOADER_MAPPING: dict[str, type[BaseLoader]] = {
    ".pdf": PyPDFLoader,
    ".docx": Docx2txtLoader,
    ".txt": TextLoader,
}

allowd_extensions = list(DOCUMENT_LOADER_MAPPING.keys())


def ownership_metadata_filter(user_id: UUID | str | None, thread_id: UUID | str | None) -> dict[str, str]:
    """Build the mandatory metadata predicate for user-owned vectors.

    Metadata is deliberately normalized as UUIDs here rather than trusting a
    caller-provided string.  A missing or malformed scope is a server-side
    configuration error and must never degrade into a broader vector search.
    """
    try:
        normalized_user_id = str(UUID(str(user_id)))
        normalized_thread_id = str(UUID(str(thread_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Both user_id and thread_id are required for vector retrieval.") from exc

    # langchain-postgres combines multiple top-level metadata fields with AND.
    return {"user_id": normalized_user_id, "thread_id": normalized_thread_id}


async def ensure_vector_store_initialized() -> None:
    """Serialize PGVector's lazy async initialization for first concurrent use."""
    async with _vector_store_initialization_lock:
        try:
            await vector_store.__apost_init__()  # type: ignore[attr-defined]
        except Exception:
            # The upstream initializer marks itself ready before its I/O has
            # completed. Reset that marker so a later request can retry.
            vector_store._async_init = False  # type: ignore[attr-defined]
            raise


async def load_scoped_vector_documents(user_id: UUID | str | None, thread_id: UUID | str | None) -> list[Document]:
    """Load every searchable chunk for one exact user-and-thread scope.

    BM25 is deliberately built from the same PGVector rows used by dense
    retrieval.  Do not relax either JSONB predicate: thread-only matching can
    expose another user's private document chunks.
    """
    metadata_filter = ownership_metadata_filter(user_id, thread_id)
    await ensure_vector_store_initialized()
    async with vector_store._make_async_session() as session:  # type: ignore[attr-defined]
        collection = await vector_store.aget_collection(session)
        if collection is None:
            return []

        statement = select(vector_store.EmbeddingStore).where(  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.collection_id == collection.uuid,  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.cmetadata["user_id"].astext == metadata_filter["user_id"],  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.cmetadata["thread_id"].astext == metadata_filter["thread_id"],  # type: ignore[attr-defined]
        )
        rows = (await session.execute(statement)).scalars().all()
        return [
            Document(
                id=str(row.id),
                page_content=row.document or "",
                metadata=row.cmetadata or {},
            )
            for row in rows
        ]


async def _load_and_split_documents(file_path: Path) -> list[Document]:
    """
    Load and split documents based on file extension.
    Raises:
        UnsupportedFileTypeError: If the file extension is not supported.
        Exception: For other loading or splitting errors.
    """

    file_extension = file_path.suffix.lower()
    if file_extension not in DOCUMENT_LOADER_MAPPING:
        raise ValueError(f"Unsupported file type: {file_extension}, Allowed types: {', '.join(allowd_extensions)}")
    loader = DOCUMENT_LOADER_MAPPING[file_extension](file_path)  # type: ignore
    documents = await loader.aload()
    splits = text_splitter.split_documents(documents)
    logger.info(f"Successfully loaded and split {file_path} into {len(splits)} chunks.")

    return splits


async def index_document_to_pgvector(file_path: Path, document_id: UUID, thread_id: UUID, user_id: UUID) -> list[str]:
    """Index a document to PGVector."""

    logger.info(f"Starting indexing for document: {file_path} with document_id: {document_id}")
    splits = await _load_and_split_documents(file_path)
    for split in splits:
        split.metadata["id"] = str(uuid4())
        split.metadata["file_name"] = file_path.name
        split.metadata["document_id"] = str(document_id)
        split.metadata["thread_id"] = str(thread_id)
        split.metadata["user_id"] = str(user_id)

    try:
        doc_ids = await vector_store.aadd_documents(splits, ids=[split.metadata["id"] for split in splits])
        logger.info(
            f"Successfully indexed {len(splits)} chunks for document {file_path} (document_id: {document_id}) to PGVector."
        )
        return doc_ids
    except Exception as e:
        logger.error(f"Error adding documents to PGVector: {e}")
        raise


async def delete_document_from_pgvector(document_ids: list[str]) -> None:
    """Delete documents from PGVector based on Document ID"""

    logger.info(f"Attempting to delete {len(document_ids)} document chunks from PGVector.")
    await vector_store.adelete(ids=document_ids)
    logger.info(f"Successfully deleted {len(document_ids)} document chunks from PGVector.")


async def delete_document_chunks_by_document_id(*, document_id: UUID, thread_id: UUID, user_id: UUID) -> int:
    """Delete every vector chunk belonging to a document without embedding a query.

    Deletion must not use similarity search: it is both incomplete for multi-chunk
    files and can fail when historical vectors use a different embedding dimension.
    """
    async with vector_store._make_async_session() as session:  # type: ignore[attr-defined]
        collection = await vector_store.aget_collection(session)
        if collection is None:
            logger.warning("PGVector collection not found while deleting document {}.", document_id)
            return 0

        statement = delete(vector_store.EmbeddingStore).where(  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.collection_id == collection.uuid,  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.cmetadata["document_id"].astext == str(document_id),  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.cmetadata["thread_id"].astext == str(thread_id),  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.cmetadata["user_id"].astext == str(user_id),  # type: ignore[attr-defined]
        )
        result = await session.execute(statement)
        await session.commit()
        deleted_count = result.rowcount or 0
        logger.info("Deleted {} PGVector chunks for document {}.", deleted_count, document_id)
        return deleted_count


async def delete_document_chunks_by_thread_id(*, thread_id: UUID, user_id: UUID) -> int:
    """Delete every vector chunk belonging to a thread without similarity search."""
    async with vector_store._make_async_session() as session:  # type: ignore[attr-defined]
        collection = await vector_store.aget_collection(session)
        if collection is None:
            logger.warning("PGVector collection not found while deleting thread {}.", thread_id)
            return 0

        statement = delete(vector_store.EmbeddingStore).where(  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.collection_id == collection.uuid,  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.cmetadata["thread_id"].astext == str(thread_id),  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.cmetadata["user_id"].astext == str(user_id),  # type: ignore[attr-defined]
        )
        result = await session.execute(statement)
        await session.commit()
        deleted_count = result.rowcount or 0
        logger.info("Deleted {} PGVector chunks for thread {}.", deleted_count, thread_id)
        return deleted_count
