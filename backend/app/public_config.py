from fastapi import APIRouter

from app.config import settings
from app.db.pgvector_utils import DOCUMENT_LOADER_MAPPING
from app.traffic_governance.dependencies import PublicConfigRateLimitDep

public_config_router = APIRouter()


@public_config_router.get("/public")
async def get_public_config(_: PublicConfigRateLimitDep):
    return {
        "model_names": settings.model_names,
        "document_extensions": list(DOCUMENT_LOADER_MAPPING),
    }
