import shutil
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile, status
from loguru import logger

from app.auth.dependencies import CurrentUserDep
from app.config import BASE_DIR
from app.db.main import SessionDep
from app.db.models import Document
from app.db.pgvector_utils import (
    DOCUMENT_LOADER_MAPPING,
    delete_document_chunks_by_document_id,
    delete_document_from_pgvector,
    index_document_to_pgvector,
    search_documents_in_pgvector,
)

from . import service as document_service
from app.threads import service as thread_service
from .schemas import DocumentDeleteResponse, DocumentPublic, DocumentUploadResponse, DocumetCreate

document_router = APIRouter()


@document_router.get("/{thread_id}", response_model=list[DocumentPublic])
async def get_documents(thread_id: UUID, current_user: CurrentUserDep, session: SessionDep):
    await thread_service.get_thread(thread_id, current_user.id, session)
    return await document_service.get_documents(thread_id, session)


@document_router.post("/upload/{thread_id}", response_model=DocumentUploadResponse)
async def upload_document(thread_id: UUID, file: UploadFile, current_user: CurrentUserDep, session: SessionDep):
    user_id = current_user.id
    await thread_service.get_thread(thread_id, user_id, session)
    if file.filename is None:
        logger.error("No file uploaded.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file uploaded.")

    allowd_extensions = list(DOCUMENT_LOADER_MAPPING.keys())
    message = f"Unsupported file type. Allowed types: {', '.join(allowd_extensions)}"
    if Path(file.filename).suffix not in allowd_extensions:
        logger.error(message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    TEMP_DIR = BASE_DIR / "tmp"
    if not TEMP_DIR.exists():
        TEMP_DIR.mkdir()
    temp_file_path = TEMP_DIR / file.filename
    document_id = None
    chunk_ids = []
    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"File '{file.filename}' saved temporarily to '{temp_file_path}'.")
        document_data = DocumetCreate(file_name=file.filename, thread_id=thread_id)
        new_document = await document_service.insert_document(document_data, session)
        document_id = new_document.id
        chunk_ids = await index_document_to_pgvector(temp_file_path, document_id, thread_id, user_id)
        await document_service.mark_document_indexed(new_document, len(chunk_ids), session)
        logger.info(f"File '{file.filename}' (document_id: {document_id}) successfully indexed to PGVector.")

        return {"document_id": document_id, "message": f"File {file.filename} uploaded and indexed successfully."}
    except Exception as e:
        logger.error("An unexpected error occurred during upload of '{}': {}", file.filename, e, exc_info=True)
        if document_id is not None:
            if chunk_ids:
                try:
                    await delete_document_from_pgvector(chunk_ids)
                    logger.info(f"Attempted cleanup of PGVector for document {document_id} after unexpected error.")
                except Exception as pgvector_clean_err:
                    logger.error(
                        f"Failed to cleanup PGVector for document {document_id} during error handling: {pgvector_clean_err}"
                    )
            failed_document = await session.get(Document, document_id)
            if failed_document is not None:
                await document_service.mark_document_failed(failed_document, str(e), session)
                logger.info(f"Marked document {document_id} as failed for administrative review.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while uploading '{file.filename}'.",
        )
    finally:
        if temp_file_path.exists():
            temp_file_path.unlink()


@document_router.delete("/{document_id}", response_model=DocumentDeleteResponse)
async def delete_document(document_id: UUID, current_user: CurrentUserDep, session: SessionDep):
    """Delete a document from the database and PGVector."""
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    await thread_service.get_thread(document.thread_id, current_user.id, session)
    deleted_chunks = await delete_document_chunks_by_document_id(document_id)
    await document_service.delete_document(document_id, session)
    logger.info(f"Successfully deleted document {document_id} from database.")
    message = f"Successfully deleted document {document_id} from database and {deleted_chunks} vector chunks"

    return {"message": f"{message}."}
