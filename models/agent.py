from datetime import datetime
from typing import Optional, Any, Dict, List
from sqlalchemy import String, Text, Integer, Float, Boolean, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class Agent(Base):
    __tablename__ = "Agent"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column("organizationId", String(36), nullable=False)
    workspace_id: Mapped[str] = mapped_column("workspaceId", String(36), nullable=False)
    user_id: Mapped[str] = mapped_column("userId", String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="IDLE")
    is_enabled: Mapped[bool] = mapped_column("isEnabled", Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    
    default_temperature: Mapped[Optional[float]] = mapped_column("defaultTemperature", Float, nullable=True, default=0.7)
    default_max_tokens: Mapped[Optional[int]] = mapped_column("defaultMaxTokens", Integer, nullable=True, default=2048)
    
    config: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    business_modules: Mapped[Optional[List[str]]] = mapped_column("businessModules", JSONB, nullable=True)
    allowed_tools: Mapped[Optional[List[str]]] = mapped_column("allowedTools", JSONB, nullable=True)
    execution_policy: Mapped[Optional[Dict[str, Any]]] = mapped_column("executionPolicy", JSONB, nullable=True)
    allowed_models: Mapped[Optional[List[str]]] = mapped_column("allowedModels", JSONB, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column("updatedAt", DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column("deletedAt", DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("workspaceId", "name", name="Agent_workspaceId_name_key"),
    )

    def __repr__(self) -> str:
        return f"<Agent(id='{self.id}', name='{self.name}', type='{self.type}')>"


class AgentHistory(Base):
    __tablename__ = "agent_histories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column("agentId", String(36), nullable=False)
    version_number: Mapped[int] = mapped_column("versionNumber", Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_enabled: Mapped[bool] = mapped_column("isEnabled", Boolean, nullable=False)
    
    config: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    business_modules: Mapped[Optional[List[str]]] = mapped_column("businessModules", JSONB, nullable=True)
    allowed_tools: Mapped[Optional[List[str]]] = mapped_column("allowedTools", JSONB, nullable=True)
    execution_policy: Mapped[Optional[Dict[str, Any]]] = mapped_column("executionPolicy", JSONB, nullable=True)
    allowed_models: Mapped[Optional[List[str]]] = mapped_column("allowedModels", JSONB, nullable=True)
    
    default_temperature: Mapped[Optional[float]] = mapped_column("defaultTemperature", Float, nullable=True)
    default_max_tokens: Mapped[Optional[int]] = mapped_column("defaultMaxTokens", Integer, nullable=True)
    
    changed_by_id: Mapped[Optional[str]] = mapped_column("changedById", String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<AgentHistory(id='{self.id}', agent_id='{self.agent_id}', version={self.version_number})>"
