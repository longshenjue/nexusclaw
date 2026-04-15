"""AI model configuration API."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models.ai_model import AIModel, UserModelPermission
from app.models.user import User
from app.dependencies import get_current_user, require_admin
from app.utils.security import encrypt_secret

router = APIRouter(prefix="/models", tags=["models"])


class ModelCreate(BaseModel):
    name: str
    provider: str  # 'anthropic' | 'openai' | 'custom'
    model_id: str
    api_key: str | None = None
    base_url: str | None = None
    is_default: bool = False
    config_json: dict = {}


class ModelResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    model_id: str
    base_url: str | None
    is_default: bool
    is_active: bool
    model_config = {"from_attributes": True}


@router.get("", response_model=list[ModelResponse])
async def list_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "admin":
        result = await db.execute(select(AIModel).where(AIModel.is_active == True))
        return result.scalars().all()

    result = await db.execute(
        select(AIModel)
        .join(UserModelPermission, AIModel.id == UserModelPermission.model_id)
        .where(UserModelPermission.user_id == current_user.id, AIModel.is_active == True)
    )
    return result.scalars().all()


@router.post("", response_model=ModelResponse, status_code=201)
async def create_model(
    data: ModelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if data.is_default:
        # Clear existing defaults
        existing_default = await db.execute(select(AIModel).where(AIModel.is_default == True))
        for m in existing_default.scalars().all():
            m.is_default = False

    model = AIModel(
        name=data.name,
        provider=data.provider,
        model_id=data.model_id,
        api_key_encrypted=encrypt_secret(data.api_key) if data.api_key else None,
        base_url=data.base_url,
        is_default=data.is_default,
        config_json=data.config_json,
        created_by=current_user.id,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


@router.delete("/{model_id}", status_code=204)
async def delete_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Not found")
    model.is_active = False
    await db.commit()


@router.post("/{model_id}/test")
async def test_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(AIModel).where(AIModel.id == model_id, AIModel.is_active == True))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Not found")

    try:
        from app.services.llm_service import get_model_stream
        stream = get_model_stream(
            provider=model.provider,
            model_id=model.model_id,
            api_key_encrypted=model.api_key_encrypted,
            base_url=model.base_url,
            messages=[{"role": "user", "content": "Say 'OK' in one word."}],
            max_tokens=10,
        )
        response_text = ""
        async for event in stream:
            if event.type == "text_delta":
                response_text += event.data["delta"]
        return {"success": True, "response": response_text}
    except Exception as e:
        return {"success": False, "error": str(e)}
