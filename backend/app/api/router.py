from fastapi import APIRouter
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.conversations import router as conv_router
from app.api.chat import router as chat_router
from app.api.datasources import router as ds_router
from app.api.knowledge import router as knowledge_router, log_router
from app.api.skills_mcp import skill_router, mcp_router
from app.api.models_api import router as models_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(admin_router)
api_router.include_router(conv_router)
api_router.include_router(chat_router)
api_router.include_router(ds_router)
api_router.include_router(knowledge_router)
api_router.include_router(log_router)
api_router.include_router(skill_router)
api_router.include_router(mcp_router)
api_router.include_router(models_router)
