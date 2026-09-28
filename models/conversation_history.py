import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ConversationHistory(Base):
    """One row per (company_id, user_id, session_id); messages accumulate in `conversation`."""

    __tablename__ = "conversation_history"
    __table_args__ = (
        UniqueConstraint("company_id", "user_id", "session_id", name="uq_conversation_history_company_user_session"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    company_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Which agent the USER is talking to for this session — O1/O2 (general orchestrators),
    # SP1-SP5 (scraper sub-agents), SM1 (social media agent). Not which agent answered.
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    conversation: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )
