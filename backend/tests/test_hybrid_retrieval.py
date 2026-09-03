from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.documents import Document

from app.chat import hybrid_retrieval
from app.db import pgvector_utils


def _document(chunk_id: str, content: str) -> Document:
    return Document(id=chunk_id, page_content=content, metadata={"id": chunk_id})


def test_bm25_tokenizer_supports_chinese_english_and_product_identifiers():
    tokens = hybrid_retrieval.tokenize_for_bm25("混合检索 supports SKU-2024X and version 2.0")

    assert "检索" in tokens
    assert "supports" in tokens
    assert "sku-2024x" in tokens
    assert "2.0" in tokens


def test_bm25_returns_only_positive_lexical_matches():
    matching = _document("matching", "The deployment code is SKU-2024X.")
    unmatched = _document("unmatched", "A completely unrelated paragraph.")

    assert hybrid_retrieval.rank_bm25_documents("SKU-2024X", [unmatched, matching], 8) == [matching]
    assert hybrid_retrieval.rank_bm25_documents("not-present-anywhere", [unmatched, matching], 8) == []


def test_weighted_rrf_deduplicates_and_favors_multi_source_candidates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_dense_weight", 1.0)
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_bm25_weight", 1.0)
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_rrf_k", 60)
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_final_k", 3)
    dense_first = _document("dense-first", "semantic")
    shared = _document("shared", "both")
    bm25_first = _document("bm25-first", "keyword")

    result = hybrid_retrieval.fuse_ranked_documents([dense_first, shared], [bm25_first, shared])

    assert [document.metadata["id"] for document in result] == ["shared", "bm25-first", "dense-first"]


def test_weighted_rrf_honors_source_weights(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_dense_weight", 2.0)
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_bm25_weight", 1.0)
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_rrf_k", 60)
    monkeypatch.setattr(hybrid_retrieval.settings, "hybrid_final_k", 3)

    result = hybrid_retrieval.fuse_ranked_documents(
        [_document("dense", "semantic")],
        [_document("bm25", "keyword")],
    )

    assert [document.metadata["id"] for document in result] == ["dense", "bm25"]


@pytest.mark.asyncio
async def test_invalid_scope_fails_before_dense_or_bm25_work(monkeypatch: pytest.MonkeyPatch):
    dense_search = AsyncMock()
    bm25_search = AsyncMock()
    monkeypatch.setattr(hybrid_retrieval, "_dense_search", dense_search)
    monkeypatch.setattr(hybrid_retrieval, "_bm25_search", bm25_search)

    with pytest.raises(ValueError, match="Both user_id and thread_id"):
        await hybrid_retrieval.retrieve_hybrid_documents("private", None, str(uuid4()))

    dense_search.assert_not_awaited()
    bm25_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_hybrid_retrieval_uses_exact_scope_and_fuses_candidates(monkeypatch: pytest.MonkeyPatch):
    user_id = str(uuid4())
    thread_id = str(uuid4())
    dense = AsyncMock(return_value=[_document("dense", "semantic"), _document("shared", "both")])
    bm25 = AsyncMock(return_value=[_document("keyword", "exact"), _document("shared", "both")])
    monkeypatch.setattr(hybrid_retrieval, "_dense_search", dense)
    monkeypatch.setattr(hybrid_retrieval, "_bm25_search", bm25)

    result = await hybrid_retrieval.retrieve_hybrid_documents("private", user_id, thread_id)

    dense.assert_awaited_once_with("private", {"user_id": user_id, "thread_id": thread_id})
    bm25.assert_awaited_once_with("private", user_id, thread_id)
    assert [document.metadata["id"] for document in result] == ["shared", "dense", "keyword"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dense_side_effect", "bm25_side_effect", "dense_result", "bm25_result", "expected"),
    [
        (RuntimeError("embedding down"), None, None, [_document("bm25", "keyword")], ["bm25"]),
        (None, RuntimeError("database down"), [_document("dense", "semantic")], None, ["dense"]),
        (None, None, [], [], []),
    ],
)
async def test_hybrid_retrieval_degrades_or_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    dense_side_effect,
    bm25_side_effect,
    dense_result,
    bm25_result,
    expected,
):
    dense = AsyncMock(side_effect=dense_side_effect, return_value=dense_result)
    bm25 = AsyncMock(side_effect=bm25_side_effect, return_value=bm25_result)
    monkeypatch.setattr(hybrid_retrieval, "_dense_search", dense)
    monkeypatch.setattr(hybrid_retrieval, "_bm25_search", bm25)

    result = await hybrid_retrieval.retrieve_hybrid_documents("private", str(uuid4()), str(uuid4()))

    assert [document.metadata["id"] for document in result] == expected


@pytest.mark.asyncio
async def test_hybrid_retrieval_fails_only_when_both_sources_fail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hybrid_retrieval, "_dense_search", AsyncMock(side_effect=RuntimeError("embedding down")))
    monkeypatch.setattr(hybrid_retrieval, "_bm25_search", AsyncMock(side_effect=RuntimeError("database down")))

    with pytest.raises(RuntimeError, match="Hybrid document retrieval failed"):
        await hybrid_retrieval.retrieve_hybrid_documents("private", str(uuid4()), str(uuid4()))


@pytest.mark.asyncio
async def test_scoped_bm25_corpus_excludes_other_users_and_threads():
    user_id = uuid4()
    thread_id = uuid4()
    await pgvector_utils.vector_store.acreate_collection()
    async with pgvector_utils.vector_store._make_async_session() as session:  # type: ignore[attr-defined]
        collection = await pgvector_utils.vector_store.aget_collection(session)
        assert collection is not None
        for metadata in (
            {"id": "owned", "user_id": str(user_id), "thread_id": str(thread_id)},
            {"id": "other-user", "user_id": str(uuid4()), "thread_id": str(thread_id)},
            {"id": "other-thread", "user_id": str(user_id), "thread_id": str(uuid4())},
        ):
            session.add(
                pgvector_utils.vector_store.EmbeddingStore(  # type: ignore[attr-defined]
                    id=str(uuid4()),
                    collection_id=collection.uuid,
                    embedding=[0.0],
                    document=metadata["id"],
                    cmetadata=metadata,
                )
            )
        await session.commit()

    documents = await pgvector_utils.load_scoped_vector_documents(user_id, thread_id)

    assert [document.metadata["id"] for document in documents] == ["owned"]
