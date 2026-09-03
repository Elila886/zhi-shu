"""Hybrid private-document retrieval over the existing PGVector collection."""

import asyncio
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

import jieba
from langchain_core.documents import Document
from loguru import logger
from rank_bm25 import BM25L

from app.config import settings
from app.db.pgvector_utils import (
    ensure_vector_store_initialized,
    load_scoped_vector_documents,
    ownership_metadata_filter,
    vector_store,
)


_SEARCHABLE_TOKEN = re.compile(r"[a-z0-9\u4e00-\u9fff]")
_COMPOUND_IDENTIFIER = re.compile(r"[a-z0-9]+(?:[-_./:][a-z0-9]+)+")


@dataclass
class _FusedCandidate:
    document: Document
    score: float = 0.0
    first_rank: int = 0


def tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize Chinese, English, and compound identifiers consistently."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens = [token.strip() for token in jieba.lcut_for_search(normalized)]
    tokens = [token for token in tokens if _SEARCHABLE_TOKEN.search(token)]
    # Keep exact product identifiers searchable in addition to jieba's parts.
    tokens.extend(_COMPOUND_IDENTIFIER.findall(normalized))
    return tokens


def rank_bm25_documents(query: str, documents: Sequence[Document], k: int) -> list[Document]:
    """Return only lexical matches, never arbitrary zero-score documents."""
    query_tokens = tokenize_for_bm25(query)
    if not query_tokens or not documents:
        return []

    corpus = [tokenize_for_bm25(document.page_content) for document in documents]
    if not any(corpus):
        return []

    # BM25L keeps an exact rare term score positive even for a tiny thread
    # corpus (where BM25Okapi can calculate a zero IDF for N=2, df=1).
    scores = BM25L(corpus).get_scores(query_tokens)
    ranked = sorted(enumerate(scores), key=lambda item: (-float(item[1]), item[0]))
    return [documents[index] for index, score in ranked if score > 0][:k]


def _document_key(document: Document) -> str | None:
    chunk_id = document.metadata.get("id")
    if chunk_id:
        return str(chunk_id)
    if document.id:
        return str(document.id)
    return None


def fuse_ranked_documents(
    dense_documents: Sequence[Document],
    bm25_documents: Sequence[Document],
) -> list[Document]:
    """Deduplicate candidates and apply weighted reciprocal-rank fusion."""
    candidates: dict[str, _FusedCandidate] = {}
    for documents, weight in (
        (dense_documents, settings.hybrid_dense_weight),
        (bm25_documents, settings.hybrid_bm25_weight),
    ):
        if weight == 0:
            continue
        seen_in_source: set[str] = set()
        for rank, document in enumerate(documents, start=1):
            key = _document_key(document)
            if key is None or key in seen_in_source:
                continue
            seen_in_source.add(key)
            candidate = candidates.setdefault(key, _FusedCandidate(document=document, first_rank=rank))
            candidate.score += weight / (settings.hybrid_rrf_k + rank)
            candidate.first_rank = min(candidate.first_rank, rank)

    ranked = sorted(candidates.items(), key=lambda item: (-item[1].score, item[1].first_rank, item[0]))
    return [candidate.document for _, candidate in ranked[: settings.hybrid_final_k]]


async def _dense_search(query: str, metadata_filter: dict[str, str]) -> list[Document]:
    await ensure_vector_store_initialized()
    return await vector_store.asimilarity_search(
        query,
        k=settings.hybrid_dense_k,
        filter=metadata_filter,
    )


async def _bm25_search(query: str, user_id: str | None, thread_id: str | None) -> list[Document]:
    documents = await load_scoped_vector_documents(user_id, thread_id)
    return await asyncio.to_thread(rank_bm25_documents, query, documents, settings.hybrid_bm25_k)


async def retrieve_hybrid_documents(
    query: str,
    user_id: str | None,
    thread_id: str | None,
) -> list[Document]:
    """Run dense and BM25 retrieval independently with safe partial fallback."""
    # Validate first so an invalid scope cannot trigger either a DB or embedding call.
    metadata_filter = ownership_metadata_filter(user_id, thread_id)
    dense_result, bm25_result = await asyncio.gather(
        _dense_search(query, metadata_filter),
        _bm25_search(query, user_id, thread_id),
        return_exceptions=True,
    )

    dense_failed = isinstance(dense_result, Exception)
    bm25_failed = isinstance(bm25_result, Exception)
    if dense_failed:
        logger.warning("Hybrid dense retrieval failed; serving BM25 result when available. error_type={}", type(dense_result).__name__)
    if bm25_failed:
        logger.warning("Hybrid BM25 retrieval failed; serving dense result when available. error_type={}", type(bm25_result).__name__)
    if dense_failed and bm25_failed:
        raise RuntimeError("Hybrid document retrieval failed")

    return fuse_ranked_documents(
        [] if dense_failed else dense_result,
        [] if bm25_failed else bm25_result,
    )
