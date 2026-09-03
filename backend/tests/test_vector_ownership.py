from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from langchain_core.documents import Document

from app.chat import tools as chat_tools
from app.db import pgvector_utils
from app.db.main import async_session
from app.db.models import Document as DatabaseDocument
from app.db.pgvector_utils import ownership_metadata_filter
from app.maintenance.backfill_vector_ownership import OwnershipScope, is_unscoped_active, reconcile_metadata

from .test_auth_integration import bearer, login, set_role, signup


@pytest.mark.asyncio
async def test_document_tool_passes_exact_user_and_thread_scope_to_hybrid_retrieval(monkeypatch: pytest.MonkeyPatch):
    user_id = uuid4()
    thread_id = uuid4()
    hybrid_retrieval = AsyncMock(return_value=[Document(page_content="allowed")])
    monkeypatch.setattr(chat_tools, "retrieve_hybrid_documents", hybrid_retrieval)
    result = await chat_tools.retrieve_user_documents.coroutine(  # type: ignore[union-attr]
        "question", {"configurable": {"user_id": str(user_id), "thread_id": str(thread_id)}}
    )

    hybrid_retrieval.assert_awaited_once_with("question", str(user_id), str(thread_id))
    assert result == "allowed"


@pytest.mark.asyncio
async def test_indexed_chunks_always_include_document_thread_and_user_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path):
    document_id = uuid4()
    thread_id = uuid4()
    user_id = uuid4()
    chunks = [Document(page_content="one", metadata={}), Document(page_content="two", metadata={})]
    add_documents = AsyncMock(return_value=[str(uuid4()), str(uuid4())])
    fake_store = SimpleNamespace(aadd_documents=add_documents)

    monkeypatch.setattr(pgvector_utils, "_load_and_split_documents", AsyncMock(return_value=chunks))
    monkeypatch.setattr(pgvector_utils, "vector_store", fake_store)

    result = await pgvector_utils.index_document_to_pgvector(tmp_path / "private.txt", document_id, thread_id, user_id)

    assert result == add_documents.return_value
    for chunk in chunks:
        assert chunk.metadata["document_id"] == str(document_id)
        assert chunk.metadata["thread_id"] == str(thread_id)
        assert chunk.metadata["user_id"] == str(user_id)


def test_ownership_metadata_filter_rejects_malformed_or_missing_scope():
    with pytest.raises(ValueError, match="Both user_id and thread_id"):
        ownership_metadata_filter(None, uuid4())
    with pytest.raises(ValueError, match="Both user_id and thread_id"):
        ownership_metadata_filter(uuid4(), "not-a-uuid")


def test_backfill_reconciles_valid_missing_mismatched_and_orphan_vectors_idempotently():
    document_id = str(uuid4())
    canonical = {document_id: OwnershipScope(thread_id=str(uuid4()), user_id=str(uuid4()))}

    valid = {"document_id": document_id, "thread_id": canonical[document_id].thread_id, "user_id": canonical[document_id].user_id}
    missing_user = {"document_id": document_id, "thread_id": canonical[document_id].thread_id}
    mismatched = {"document_id": document_id, "thread_id": str(uuid4()), "user_id": str(uuid4())}
    orphan = {"document_id": str(uuid4()), "thread_id": str(uuid4()), "user_id": str(uuid4())}

    assert reconcile_metadata(valid, canonical).category == "already_valid"
    repaired_missing = reconcile_metadata(missing_user, canonical)
    repaired_mismatched = reconcile_metadata(mismatched, canonical)
    quarantined = reconcile_metadata(orphan, canonical)

    assert repaired_missing.category == "repaired"
    assert repaired_mismatched.category == "repaired"
    assert repaired_missing.metadata["user_id"] == canonical[document_id].user_id
    assert repaired_mismatched.metadata["thread_id"] == canonical[document_id].thread_id
    assert quarantined.category == "quarantined"
    assert "user_id" not in quarantined.metadata
    assert "thread_id" not in quarantined.metadata
    assert quarantined.metadata["isolation_quarantined"] is True
    assert reconcile_metadata(repaired_missing.metadata, canonical).category == "already_valid"
    assert reconcile_metadata(quarantined.metadata, canonical).category == "already_quarantined"
    assert is_unscoped_active(missing_user) is True
    assert is_unscoped_active(quarantined.metadata) is False


@pytest.mark.asyncio
async def test_document_and_thread_cleanup_receive_verified_ownership_scope(client, monkeypatch: pytest.MonkeyPatch):
    from app.documents import routes as document_routes
    from app.threads import routes as thread_routes
    from app.threads import service as thread_service

    owner = await signup(client, "cleanupowner")
    owner_login = await login(client, "cleanupowner")
    thread_response = await client.post("/api/v1/threads/", headers=bearer(owner_login))
    assert thread_response.status_code == 201
    thread_id = thread_response.json()["id"]
    thread_uuid = UUID(thread_id)
    owner_uuid = UUID(owner["id"])
    async with async_session() as session:
        document = DatabaseDocument(file_name="owned.txt", thread_id=thread_uuid, status="completed")
        session.add(document)
        await session.commit()
        await session.refresh(document)
        document_id = document.id

    delete_document_vectors = AsyncMock(return_value=0)
    delete_thread_vectors = AsyncMock(return_value=0)
    checkpointer = AsyncMock()
    monkeypatch.setattr(document_routes, "delete_document_chunks_by_document_id", delete_document_vectors)
    monkeypatch.setattr(thread_service, "delete_document_chunks_by_thread_id", delete_thread_vectors)
    monkeypatch.setattr(thread_routes, "get_checkpointer", AsyncMock(return_value=checkpointer))

    deleted_document = await client.delete(f"/api/v1/documents/{document_id}", headers=bearer(owner_login))
    assert deleted_document.status_code == 200
    delete_document_vectors.assert_awaited_once_with(
        document_id=document_id,
        thread_id=thread_uuid,
        user_id=owner_uuid,
    )

    deleted_thread = await client.delete(f"/api/v1/threads/{thread_id}", headers=bearer(owner_login))
    assert deleted_thread.status_code == 204
    delete_thread_vectors.assert_awaited_once_with(thread_id=thread_uuid, user_id=owner_uuid)


@pytest.mark.asyncio
async def test_admin_document_cleanup_resolves_the_document_owner(client, monkeypatch: pytest.MonkeyPatch):
    from app.admin import routes as admin_routes

    owner = await signup(client, "adown")
    await signup(client, "adroot")
    await set_role("adroot@example.com", "super_admin")
    owner_login = await login(client, "adown")
    root_login = await login(client, "adroot", admin=True)
    thread_response = await client.post("/api/v1/threads/", headers=bearer(owner_login))
    assert thread_response.status_code == 201
    thread_id = thread_response.json()["id"]
    thread_uuid = UUID(thread_id)
    owner_uuid = UUID(owner["id"])
    async with async_session() as session:
        document = DatabaseDocument(file_name="admin-owned.txt", thread_id=thread_uuid, status="completed")
        session.add(document)
        await session.commit()
        await session.refresh(document)
        document_id = document.id

    delete_document_vectors = AsyncMock(return_value=0)
    monkeypatch.setattr(admin_routes, "delete_document_chunks_by_document_id", delete_document_vectors)

    response = await client.delete(f"/api/v1/admin/documents/{document_id}", headers=bearer(root_login))
    assert response.status_code == 200
    delete_document_vectors.assert_awaited_once_with(
        document_id=document_id,
        thread_id=thread_uuid,
        user_id=owner_uuid,
    )
