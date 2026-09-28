from datetime import datetime
from typing import Optional
from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

class OrchestratorTask(Base):
    __tablename__ = "orchestrator_tasks"

    task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_query: Mapped[str] = mapped_column(Text, nullable=False)
    company_id: Mapped[str] = mapped_column(String(255), nullable=False)
    clarifying_answers: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(50), default="pending_clarification", server_default="pending_clarification")
    current_step: Mapped[str] = mapped_column(String(255), default="Created", server_default="Created")
    final_result: Mapped[dict] = mapped_column(JSON, nullable=True)
    
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    task_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<OrchestratorTask(task_id='{self.task_id}', status='{self.status}', current_step='{self.current_step}')>"
