from fastapi import FastAPI

from app.auth.routes import auth_router
from app.admin.routes import admin_router
from app.chat.routes import chat_router
from app.documents.routes import document_router
from app.lifespan import lifespan
from app.middleware import register_middleware
from app.public_config import public_config_router
from app.leave.routes import admin_leave_router, leave_router
from app.threads.routes import thread_router
from app.users.routes import user_router
from app.traffic_governance.errors import TrafficGovernanceError, traffic_governance_exception_handler

version = "v1"
version_prefix = f"/api/{version}"

app = FastAPI(
    title="RAG Chatbot API",
    description="RAG Chatbot API",
    version=version,
    openapi_url=f"{version_prefix}/openapi.json",
    docs_url=f"{version_prefix}/docs",
    redoc_url=f"{version_prefix}/redoc",
    lifespan=lifespan,
    license_info={"name": "MIT License", "url": "https://opensource.org/license/mit"},
)

register_middleware(app)
app.add_exception_handler(TrafficGovernanceError, traffic_governance_exception_handler)

app.include_router(auth_router, prefix=f"{version_prefix}/auth", tags=["AUTH"])
app.include_router(user_router, prefix=f"{version_prefix}/users", tags=["USERS"])
app.include_router(thread_router, prefix=f"{version_prefix}/threads", tags=["THREADS"])
app.include_router(chat_router, prefix=f"{version_prefix}/chat", tags=["CHAT"])
app.include_router(document_router, prefix=f"{version_prefix}/documents", tags=["DOCUMENTS"])
app.include_router(admin_router, prefix=f"{version_prefix}/admin", tags=["ADMIN"])
app.include_router(public_config_router, prefix=f"{version_prefix}/config", tags=["CONFIG"])
app.include_router(leave_router, prefix=f"{version_prefix}/leave", tags=["LEAVE"])
app.include_router(admin_leave_router, prefix=f"{version_prefix}/admin", tags=["ADMIN LEAVE"])
