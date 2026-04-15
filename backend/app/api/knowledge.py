"""Knowledge base and log source management API."""
import uuid
import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models.knowledge import KnowledgeSource, UserKnowledgePermission, LogSource, UserLogPermission
from app.models.user import User
from app.dependencies import get_current_user, require_admin
from app.config import settings

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
log_router = APIRouter(prefix="/logs", tags=["logs"])


class KnowledgeResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    status: str
    chunk_count: int | None
    error_msg: str | None
    model_config = {"from_attributes": True}


class RepoIngestRequest(BaseModel):
    repo_url: str
    branch: str = "main"
    access_token: str | None = None
    name: str | None = None


async def _run_clone_task(source_id: str):
    """Background task: clone a git repository to local disk."""
    from app.database import AsyncSessionLocal
    from app.services.knowledge_service import clone_repo
    import logging
    logger = logging.getLogger("app.knowledge")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
        source = result.scalar_one_or_none()
        if not source:
            return

        source.status = "cloning"
        await db.commit()

        try:
            clone_path = await clone_repo(source)
            source.clone_path = clone_path
            source.status = "ready"
            logger.info("repo_cloned | name=%s path=%s", source.name, clone_path)
        except Exception as e:
            source.status = "error"
            source.error_msg = str(e)[:500]
            logger.error("repo_clone_failed | name=%s error=%s", source.name, str(e)[:200])

        await db.commit()


@router.get("", response_model=list[KnowledgeResponse])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "admin":
        result = await db.execute(select(KnowledgeSource))
        return result.scalars().all()

    result = await db.execute(
        select(KnowledgeSource)
        .join(UserKnowledgePermission, KnowledgeSource.id == UserKnowledgePermission.knowledge_id)
        .where(UserKnowledgePermission.user_id == current_user.id)
    )
    return result.scalars().all()


@router.post("/upload", response_model=KnowledgeResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename or "")[1].lower().lstrip(".")
    if ext not in ("pdf", "md", "txt", "markdown"):
        raise HTTPException(400, f"Unsupported file type: {ext}")

    # Save file
    user_upload_dir = os.path.join(settings.upload_dir, str(current_user.id))
    os.makedirs(user_upload_dir, exist_ok=True)
    file_path = os.path.join(user_upload_dir, file.filename or "upload")

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    source = KnowledgeSource(
        name=file.filename or "upload",
        type="document",
        file_path=file_path,
        file_type="md" if ext in ("md", "markdown") else ext,
        file_size=len(content),
        status="ready",   # File is saved — immediately readable via read_knowledge_document
        created_by=current_user.id,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.post("/repo", response_model=KnowledgeResponse, status_code=201)
async def ingest_repo(
    background_tasks: BackgroundTasks,
    data: RepoIngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.utils.security import encrypt_secret

    name = data.name or data.repo_url.rstrip("/").split("/")[-1]

    source = KnowledgeSource(
        name=name,
        type="github_repo",
        repo_url=data.repo_url,
        branch=data.branch,
        access_token_encrypted=encrypt_secret(data.access_token) if data.access_token else None,
        status="pending",
        created_by=current_user.id,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    background_tasks.add_task(_run_clone_task, str(source.id))
    return source


@router.get("/{source_id}/status")
async def get_status(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Not found")
    return {"status": source.status, "chunk_count": source.chunk_count, "error": source.error_msg}


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(KnowledgeSource).where(KnowledgeSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Not found")

    # Remove cloned repo directory if present
    if source.clone_path and os.path.exists(source.clone_path):
        shutil.rmtree(source.clone_path, ignore_errors=True)

    await db.delete(source)
    await db.commit()


# ─── Log Sources ─────────────────────────────────────────────────────────────

class LogSourceCreate(BaseModel):
    name: str
    description: str | None = None  # label hints for AI, e.g. 'server="myapp", env="prod"'
    type: str  # 'file' | 'elasticsearch' | 'loki'
    file_pattern: str | None = None
    es_host: str | None = None
    es_port: int | None = None
    es_index_pattern: str | None = None
    es_username: str | None = None
    es_password: str | None = None
    loki_url: str | None = None
    loki_username: str | None = None
    loki_password: str | None = None
    loki_token: str | None = None


@log_router.get("/sources")
async def list_log_sources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == "admin":
        result = await db.execute(select(LogSource).where(LogSource.is_active == True))
        sources = result.scalars().all()
    else:
        result = await db.execute(
            select(LogSource)
            .join(UserLogPermission, LogSource.id == UserLogPermission.log_id)
            .where(UserLogPermission.user_id == current_user.id, LogSource.is_active == True)
        )
        sources = result.scalars().all()
    return [{"id": str(s.id), "name": s.name, "type": s.type, "description": s.description or ""} for s in sources]


@log_router.post("/sources", status_code=201)
async def create_log_source(
    data: LogSourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    import json
    from app.utils.security import encrypt_secret
    es_creds = None
    if data.es_username and data.es_password:
        es_creds = encrypt_secret(json.dumps({"username": data.es_username, "password": data.es_password}))

    loki_creds = None
    if data.loki_token:
        loki_creds = encrypt_secret(json.dumps({"token": data.loki_token}))
    elif data.loki_username and data.loki_password:
        loki_creds = encrypt_secret(json.dumps({"username": data.loki_username, "password": data.loki_password}))

    source = LogSource(
        name=data.name,
        description=data.description,
        type=data.type,
        file_pattern=data.file_pattern,
        es_host=data.es_host,
        es_port=data.es_port,
        es_index_pattern=data.es_index_pattern,
        es_credentials_encrypted=es_creds,
        loki_url=data.loki_url,
        loki_credentials_encrypted=loki_creds,
        created_by=current_user.id,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return {"id": str(source.id), "name": source.name, "type": source.type}


@log_router.delete("/sources/{source_id}", status_code=204)
async def delete_log_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(LogSource).where(LogSource.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Not found")
    await db.delete(source)
    await db.commit()


@log_router.post("/search")
async def search_logs(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import re
    from app.services.log_service import search_logs as _search
    result = await _search(
        source_id=data.get("source_id"),
        query=data.get("query", ""),
        level=data.get("level"),
        time_range=data.get("time_range", "1h"),
        limit=int(data.get("limit", 100)),
        user=current_user,
        db=db,
    )

    # Parse the text result into structured entries for the UI.
    # Each line has the format: [YYYY-MM-DD HH:MM:SS] [labels] message
    entries = []
    pattern = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(?:\[([^\]]*)\]\s*)?(.*)$')
    for line in result.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if m:
            entries.append({
                "timestamp": m.group(1),
                "level": "",
                "message": m.group(3),
                "source": m.group(2) or "",
            })
        else:
            entries.append({"timestamp": "", "level": "", "message": line, "source": ""})

    return {"results": entries}
