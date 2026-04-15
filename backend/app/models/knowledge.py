import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'document' | 'github_repo' | 'conversation'
    # document
    file_path: Mapped[str | None] = mapped_column(String(500))
    file_type: Mapped[str | None] = mapped_column(String(20))
    file_size: Mapped[int | None] = mapped_column(Integer)
    # github / git repo
    repo_url: Mapped[str | None] = mapped_column(String(500))
    branch: Mapped[str | None] = mapped_column(String(100))
    github_token_encrypted: Mapped[str | None] = mapped_column(Text)  # kept for backward compat
    clone_path: Mapped[str | None] = mapped_column(String(500))
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # status
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|cloning|ready|error
    error_msg: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserKnowledgePermission(Base):
    __tablename__ = "user_knowledge_permissions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    knowledge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="CASCADE"), primary_key=True)


class LogSource(Base):
    __tablename__ = "log_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)  # label hints for AI, e.g. server="myapp"
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'file' | 'elasticsearch' | 'loki'
    file_pattern: Mapped[str | None] = mapped_column(String(500))
    es_host: Mapped[str | None] = mapped_column(String(255))
    es_port: Mapped[int | None] = mapped_column(Integer)
    es_index_pattern: Mapped[str | None] = mapped_column(String(255))
    es_credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    loki_url: Mapped[str | None] = mapped_column(String(500))
    loki_credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserLogPermission(Base):
    __tablename__ = "user_log_permissions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    log_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("log_sources.id", ondelete="CASCADE"), primary_key=True)
