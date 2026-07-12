from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ADMIN_ROLES = ("owner", "finance", "support", "operator", "viewer")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    sessions = relationship("AdminSession", back_populates="admin_user", cascade="all, delete-orphan")
    audit_events = relationship("AdminAuditEvent", back_populates="admin_user")


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    user_agent_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    csrf_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    admin_user = relationship("AdminUser", back_populates="sessions")


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="attempted")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    admin_user = relationship("AdminUser", back_populates="audit_events")

    __table_args__ = (
        Index("ix_admin_audit_target", "target_type", "target_id"),
        Index("ix_admin_audit_action_created", "action", "created_at"),
    )
