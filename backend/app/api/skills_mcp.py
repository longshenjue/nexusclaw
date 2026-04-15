"""SkillHub and MCPHub management APIs."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from pydantic import BaseModel
from app.database import get_db
from app.models.skill_mcp import Skill, UserSkillPermission, MCPServer, UserMCPPermission
from app.models.user import User
from app.dependencies import get_current_user, require_admin

skill_router = APIRouter(prefix="/skills", tags=["skills"])
mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])


class SkillCreate(BaseModel):
    name: str
    description: str
    category: str | None = None
    type: str = "system_prompt"
    system_prompt: str | None = None
    workflow_json: dict | None = None
    parameters_schema: dict = {}
    is_public: bool = False


class SkillResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    category: str | None
    type: str
    is_public: bool
    parameters_schema: dict
    model_config = {"from_attributes": True}


@skill_router.get("", response_model=list[SkillResponse])
async def list_skills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "admin":
        result = await db.execute(select(Skill))
        return result.scalars().all()

    result = await db.execute(
        select(Skill).where(
            or_(
                Skill.is_public == True,
                Skill.created_by == current_user.id,
                Skill.id.in_(
                    select(UserSkillPermission.skill_id).where(UserSkillPermission.user_id == current_user.id)
                ),
            )
        )
    )
    return result.scalars().all()


@skill_router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skill = Skill(
        name=data.name,
        description=data.description,
        category=data.category,
        type=data.type,
        system_prompt=data.system_prompt,
        workflow_json=data.workflow_json,
        parameters_schema=data.parameters_schema,
        is_public=data.is_public if current_user.role == "admin" else False,
        created_by=current_user.id,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill


@skill_router.get("/{skill_id}")
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


@skill_router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: uuid.UUID,
    data: SkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Skill).where(Skill.id == skill_id, Skill.created_by == current_user.id)
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Skill not found or not yours")

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(skill, field, value)
    await db.commit()
    await db.refresh(skill)
    return skill


@skill_router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Skill).where(Skill.id == skill_id)
    if current_user.role != "admin":
        query = query.where(Skill.created_by == current_user.id)
    result = await db.execute(query)
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "Not found")
    await db.delete(skill)
    await db.commit()


# ─── MCP Hub ─────────────────────────────────────────────────────────────────

class MCPServerCreate(BaseModel):
    name: str
    type: str = "builtin"
    builtin_key: str | None = None
    command: str | None = None
    args: list | None = None
    env_vars: dict | None = None
    transport: str = "stdio"
    endpoint_url: str | None = None


@mcp_router.get("")
async def list_mcp_servers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "admin":
        result = await db.execute(select(MCPServer).where(MCPServer.is_active == True))
        servers = result.scalars().all()
    else:
        result = await db.execute(
            select(MCPServer, UserMCPPermission)
            .join(UserMCPPermission, MCPServer.id == UserMCPPermission.mcp_id)
            .where(UserMCPPermission.user_id == current_user.id, MCPServer.is_active == True)
        )
        rows = result.all()
        servers = [row[0] for row in rows]

    return [
        {"id": str(s.id), "name": s.name, "type": s.type, "builtin_key": s.builtin_key}
        for s in servers
    ]


@mcp_router.post("", status_code=201)
async def create_mcp_server(
    data: MCPServerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    server = MCPServer(
        name=data.name,
        type=data.type,
        builtin_key=data.builtin_key,
        command=data.command,
        args=data.args,
        env_vars=data.env_vars,
        transport=data.transport,
        endpoint_url=data.endpoint_url,
        created_by=current_user.id,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return {"id": str(server.id), "name": server.name}


@mcp_router.delete("/{mcp_id}", status_code=204)
async def delete_mcp_server(
    mcp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(MCPServer).where(MCPServer.id == mcp_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(404, "Not found")
    server.is_active = False
    await db.commit()
