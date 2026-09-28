import uuid
from datetime import datetime
from typing import Optional, Any
from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organizationId: Mapped[str] = mapped_column("organizationId", String(255), nullable=False)
    workspaceId: Mapped[Optional[str]] = mapped_column("workspaceId", String(255), nullable=True)
    userId: Mapped[Optional[str]] = mapped_column("userId", String(255), nullable=True)
    ipAddress: Mapped[Optional[str]] = mapped_column("ipAddress", String(255), nullable=True)
    device: Mapped[Optional[str]] = mapped_column("device", String(100), nullable=True, default="Server")
    browser: Mapped[Optional[str]] = mapped_column("browser", String(100), nullable=True, default="Python Engine")
    module: Mapped[str] = mapped_column("module", String(50), nullable=False)
    action: Mapped[str] = mapped_column("action", String(50), nullable=False)
    resource: Mapped[Optional[str]] = mapped_column("resource", String(255), nullable=True)
    oldValue: Mapped[Optional[Any]] = mapped_column("oldValue", JSON, nullable=True)
    newValue: Mapped[Optional[Any]] = mapped_column("newValue", JSON, nullable=True)
    createdAt: Mapped[datetime] = mapped_column("createdAt", DateTime, default=func.now(), server_default=func.now())
    deletedAt: Mapped[Optional[datetime]] = mapped_column("deletedAt", DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<AuditLog(id='{self.id}', module='{self.module}', action='{self.action}')>"
