import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, ForeignKey, func, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class Datasource(Base):
    __tablename__ = "datasources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=3306)
    database_name: Mapped[str] = mapped_column(String(100), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Cached full schema (tables + columns). Populated on create/test and via refresh endpoint.
    schema_cache: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    schema_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserDatasourcePermission(Base):
    __tablename__ = "user_datasource_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    datasource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasources.id", ondelete="CASCADE"), nullable=False)
    allowed_tables: Mapped[list | None] = mapped_column(JSONB)  # null = all
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
