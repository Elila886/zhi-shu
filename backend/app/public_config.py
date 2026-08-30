from fastapi import APIRouter

from app.config import settings
from app.db.pgvector_utils import DOCUMENT_LOADER_MAPPING

public_config_router = APIRouter()


@public_config_router.get("/public")
async def get_public_config():
    return {
        "model_names": settings.model_names,
        "document_extensions": list(DOCUMENT_LOADER_MAPPING),
    }
