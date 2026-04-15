"""Datasource management API."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db, AsyncSessionLocal
from app.models.datasource import Datasource, UserDatasourcePermission
from app.models.user import User
from app.dependencies import get_current_user, require_admin
from app.utils.security import encrypt_secret

router = APIRouter(prefix="/datasources", tags=["datasources"])
logger = logging.getLogger(__name__)


async def _cache_schema_bg(ds_id: uuid.UUID):
    """Background task: fetch full schema and store in datasource.schema_cache."""
    from app.services.mysql_service import fetch_full_schema
    from datetime import datetime, timezone

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Datasource).where(Datasource.id == ds_id))
        ds = result.scalar_one_or_none()
        if not ds:
            return
        try:
            schema = await fetch_full_schema(ds)
            ds.schema_cache = schema
            ds.schema_cached_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception as e:
            logger.warning("schema_cache_bg | ds=%s failed: %s", ds.name, e)


class DatasourceCreate(BaseModel):
    name: str
    host: str
    port: int = 3306
    database_name: str
    username: str
    password: str
    ssl_enabled: bool = False


class DatasourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    host: str
    port: int
    database_name: str
    username: str
    ssl_enabled: bool
    is_active: bool
    model_config = {"from_attributes": True}


@router.get("", response_model=list[DatasourceResponse])
async def list_datasources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "admin":
        result = await db.execute(select(Datasource).where(Datasource.is_active == True))
        return result.scalars().all()

    result = await db.execute(
        select(Datasource)
        .join(UserDatasourcePermission, Datasource.id == UserDatasourcePermission.datasource_id)
        .where(UserDatasourcePermission.user_id == current_user.id, Datasource.is_active == True)
    )
    return result.scalars().all()


@router.post("", response_model=DatasourceResponse, status_code=201)
async def create_datasource(
    data: DatasourceCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    ds = Datasource(
        name=data.name,
        host=data.host,
        port=data.port,
        database_name=data.database_name,
        username=data.username,
        password_encrypted=encrypt_secret(data.password),
        ssl_enabled=data.ssl_enabled,
        created_by=current_user.id,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    # Asynchronously fetch and cache the full schema so chat has it immediately
    background_tasks.add_task(_cache_schema_bg, ds.id)
    return ds


@router.delete("/{ds_id}", status_code=204)
async def delete_datasource(
    ds_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(Datasource).where(Datasource.id == ds_id))
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Not found")
    ds.is_active = False
    await db.commit()


@router.post("/{ds_id}/test")
async def test_datasource(
    ds_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.services.mysql_service import test_connection
    result = await db.execute(select(Datasource).where(Datasource.id == ds_id))
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Not found")
    ok, message = await test_connection(ds)
    # Refresh schema cache whenever connection is tested (schema may have changed)
    if ok:
        background_tasks.add_task(_cache_schema_bg, ds.id)
    return {"success": ok, "message": message}


@router.post("/{ds_id}/refresh-schema", status_code=202)
async def refresh_schema(
    ds_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Manually trigger a schema cache refresh for a datasource."""
    result = await db.execute(select(Datasource).where(Datasource.id == ds_id))
    ds = result.scalar_one_or_none()
    if not ds:
        raise HTTPException(404, "Not found")
    background_tasks.add_task(_cache_schema_bg, ds.id)
    return {"message": "Schema refresh queued"}


@router.get("/{ds_id}/schema")
async def get_schema(
    ds_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.mysql_service import list_tables, get_table_schema
    tables = await list_tables(str(ds_id), current_user, db)
    return {"tables": tables}
