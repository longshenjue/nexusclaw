from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.user import User
from app.models.skill_mcp import AuditLog
from app.schemas.auth import UserResponse, UserUpdate
from app.dependencies import require_admin
import uuid

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    data: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.utils.security import hash_password
    user = User(
        email=data["email"],
        username=data["username"],
        hashed_password=hash_password(data["password"]),
        role=data.get("role", "user"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(user)
    await db.commit()


@router.get("/users/{user_id}/permissions")
async def get_permissions(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.models.datasource import UserDatasourcePermission
    from app.models.knowledge import UserKnowledgePermission, UserLogPermission
    from app.models.skill_mcp import UserSkillPermission, UserMCPPermission
    from app.models.ai_model import UserModelPermission

    ds = await db.execute(select(UserDatasourcePermission).where(UserDatasourcePermission.user_id == user_id))
    kn = await db.execute(select(UserKnowledgePermission).where(UserKnowledgePermission.user_id == user_id))
    lg = await db.execute(select(UserLogPermission).where(UserLogPermission.user_id == user_id))
    sk = await db.execute(select(UserSkillPermission).where(UserSkillPermission.user_id == user_id))
    mc = await db.execute(select(UserMCPPermission).where(UserMCPPermission.user_id == user_id))
    mo = await db.execute(select(UserModelPermission).where(UserModelPermission.user_id == user_id))

    return {
        "datasources": [str(r.datasource_id) for r in ds.scalars().all()],
        "knowledge": [str(r.knowledge_id) for r in kn.scalars().all()],
        "log_sources": [str(r.log_id) for r in lg.scalars().all()],
        "skills": [str(r.skill_id) for r in sk.scalars().all()],
        "mcp_servers": [str(r.mcp_id) for r in mc.scalars().all()],
        "models": [str(r.model_id) for r in mo.scalars().all()],
    }


@router.post("/users/{user_id}/permissions")
async def assign_permissions(
    user_id: uuid.UUID,
    data: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """
    Bulk assign permissions. data format:
    {
      "datasources": [{"datasource_id": "uuid", "allowed_tables": null, "can_write": false}],
      "knowledge": ["uuid", ...],
      "log_sources": ["uuid", ...],
      "skills": ["uuid", ...],
      "mcp_servers": [{"mcp_id": "uuid", "allowed_tools": null}],
      "models": ["uuid", ...]
    }
    """
    from app.models.datasource import UserDatasourcePermission
    from app.models.knowledge import UserKnowledgePermission, UserLogPermission
    from app.models.skill_mcp import UserSkillPermission, UserMCPPermission
    from app.models.ai_model import UserModelPermission
    from sqlalchemy import delete

    # Clear existing permissions for simplicity (replace strategy)
    await db.execute(delete(UserDatasourcePermission).where(UserDatasourcePermission.user_id == user_id))
    await db.execute(delete(UserKnowledgePermission).where(UserKnowledgePermission.user_id == user_id))
    await db.execute(delete(UserLogPermission).where(UserLogPermission.user_id == user_id))
    await db.execute(delete(UserSkillPermission).where(UserSkillPermission.user_id == user_id))
    await db.execute(delete(UserMCPPermission).where(UserMCPPermission.user_id == user_id))
    await db.execute(delete(UserModelPermission).where(UserModelPermission.user_id == user_id))

    for ds in data.get("datasources", []):
        db.add(UserDatasourcePermission(
            user_id=user_id,
            datasource_id=ds["datasource_id"],
            allowed_tables=ds.get("allowed_tables"),
            can_write=ds.get("can_write", False),
        ))
    for k_id in data.get("knowledge", []):
        db.add(UserKnowledgePermission(user_id=user_id, knowledge_id=k_id))
    for l_id in data.get("log_sources", []):
        db.add(UserLogPermission(user_id=user_id, log_id=l_id))
    for s_id in data.get("skills", []):
        db.add(UserSkillPermission(user_id=user_id, skill_id=s_id))
    for mcp in data.get("mcp_servers", []):
        db.add(UserMCPPermission(user_id=user_id, mcp_id=mcp["mcp_id"], allowed_tools=mcp.get("allowed_tools")))
    for m_id in data.get("models", []):
        db.add(UserModelPermission(user_id=user_id, model_id=m_id))

    await db.commit()
    return {"status": "ok"}


@router.get("/audit-logs")
async def get_audit_logs(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "user_id": str(log.user_id) if log.user_id else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details_json": log.details_json,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
