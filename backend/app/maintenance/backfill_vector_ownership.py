"""Audit and repair ownership metadata in the configured PGVector collection.

Run from ``backend`` or inside the backend image:

    python -m app.maintenance.backfill_vector_ownership
    python -m app.maintenance.backfill_vector_ownership --apply

The default is read-only.  ``--apply`` only repairs vectors that can be
authoritatively mapped through Document -> Thread -> User.  Orphaned vectors
are quarantined by removing their searchable ownership fields; their content
is deliberately retained for manual review.
"""

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.db.main import async_session
from app.db.models import Document, Thread
from app.db.pgvector_utils import vector_store


@dataclass(frozen=True)
class OwnershipScope:
    thread_id: str
    user_id: str


@dataclass(frozen=True)
class Reconciliation:
    category: str
    metadata: dict[str, Any]
    changed: bool


def reconcile_metadata(metadata: Any, ownership_by_document: dict[str, OwnershipScope]) -> Reconciliation:
    """Return the canonical, non-searchable, or unchanged metadata for one vector."""
    original = metadata if isinstance(metadata, dict) else {}
    updated = dict(original)
    document_id = updated.get("document_id")
    scope = ownership_by_document.get(str(document_id)) if document_id else None

    if scope is None:
        updated.pop("user_id", None)
        updated.pop("thread_id", None)
        updated["isolation_quarantined"] = True
        changed = updated != original
        return Reconciliation("quarantined" if changed else "already_quarantined", updated, changed)

    updated["thread_id"] = scope.thread_id
    updated["user_id"] = scope.user_id
    updated.pop("isolation_quarantined", None)
    changed = updated != original
    return Reconciliation("repaired" if changed else "already_valid", updated, changed)


def is_unscoped_active(metadata: Any) -> bool:
    """Whether metadata could be searched without a complete ownership scope."""
    if not isinstance(metadata, dict):
        return True
    if metadata.get("isolation_quarantined") is True:
        return False
    return not metadata.get("user_id") or not metadata.get("thread_id")


async def canonical_ownership() -> dict[str, OwnershipScope]:
    """Load the relational database as the authoritative ownership source."""
    async with async_session() as session:
        rows = await session.execute(
            select(Document.id, Document.thread_id, Thread.user_id).join(Thread, Document.thread_id == Thread.id)
        )
        return {
            str(document_id): OwnershipScope(thread_id=str(thread_id), user_id=str(user_id))
            for document_id, thread_id, user_id in rows
        }


async def audit_vector_ownership(apply: bool = False) -> dict[str, int | bool]:
    """Audit, and optionally reconcile, every vector in the configured collection."""
    ownership_by_document = await canonical_ownership()
    counts: Counter[str] = Counter()
    unscoped_active = 0

    async with vector_store._make_async_session() as session:  # type: ignore[attr-defined]
        collection = await vector_store.aget_collection(session)
        if collection is None:
            return {"apply": apply, "collection_found": False, "unscoped_active": 0}

        statement = select(vector_store.EmbeddingStore).where(  # type: ignore[attr-defined]
            vector_store.EmbeddingStore.collection_id == collection.uuid  # type: ignore[attr-defined]
        )
        vectors = (await session.execute(statement)).scalars().all()
        for vector in vectors:
            reconciliation = reconcile_metadata(vector.cmetadata, ownership_by_document)
            counts[reconciliation.category] += 1
            if apply and reconciliation.changed:
                vector.cmetadata = reconciliation.metadata
            metadata_for_audit = reconciliation.metadata if apply else vector.cmetadata
            unscoped_active += int(is_unscoped_active(metadata_for_audit))

        if apply:
            await session.commit()

    return {
        "apply": apply,
        "collection_found": True,
        "already_valid": counts["already_valid"],
        "repaired": counts["repaired"],
        "quarantined": counts["quarantined"],
        "already_quarantined": counts["already_quarantined"],
        "unscoped_active": unscoped_active,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit or repair PGVector ownership metadata.")
    parser.add_argument("--apply", action="store_true", help="Persist repairs and quarantine orphaned vectors.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await audit_vector_ownership(apply=args.apply)
    print(json.dumps(result, sort_keys=True))
    if args.apply and result["unscoped_active"] != 0:
        raise SystemExit("Ownership reconciliation left active vectors without a complete scope.")


if __name__ == "__main__":
    asyncio.run(main())
