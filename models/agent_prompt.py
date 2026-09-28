import asyncio
import time

from sqlalchemy import String, Text, Integer, Boolean, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base
from models.engine import get_async_session

class AgentPrompt(Base):
    __tablename__ = "agent_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_key: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Version control columns
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # Each (agent_id, prompt_key, version) trio must be unique
        UniqueConstraint("agent_id", "prompt_key", "version", name="uq_agent_prompt_version"),
    )

    def __repr__(self) -> str:
        return f"<AgentPrompt(id={self.id}, agent_id='{self.agent_id}', prompt_key='{self.prompt_key}', version='{self.version}', is_active={self.is_active})>"


# Each fetch is a DB round-trip (0.8-2s against the pooled Neon endpoint) paid on every LLM call.
# ponytail: per-process TTL cache, so a prompt edit reaches every worker within PROMPT_CACHE_SECONDS.
PROMPT_CACHE_SECONDS = 60
_prompt_cache: dict[tuple[str, str], tuple[float, str]] = {}
# Concurrent misses (e.g. many document chunks analyzed at once) share one DB fetch instead of
# each taking a connection from the small pool.
_prompt_fetch_lock = asyncio.Lock()


async def fetch_prompt(agent_id: str, prompt_key: str) -> str:
    """Fetches the currently active prompt for a given agent_id and prompt_key."""
    key = (agent_id, prompt_key)
    cached = _prompt_cache.get(key)
    if cached and time.monotonic() - cached[0] < PROMPT_CACHE_SECONDS:
        return cached[1]
    async with _prompt_fetch_lock:
        cached = _prompt_cache.get(key)
        if cached and time.monotonic() - cached[0] < PROMPT_CACHE_SECONDS:
            return cached[1]
        prompt = await _fetch_prompt_uncached(agent_id, prompt_key)
        _prompt_cache[key] = (time.monotonic(), prompt)
        return prompt


async def _fetch_prompt_uncached(agent_id: str, prompt_key: str) -> str:
    async with get_async_session() as session:
        stmt = select(AgentPrompt.prompt).where(
            AgentPrompt.agent_id == agent_id,
            AgentPrompt.prompt_key == prompt_key,
            AgentPrompt.is_active == True,  # noqa: E712
        )
        res = await session.execute(stmt)
        prompt = res.scalar_one_or_none()
        if prompt is None:
            raise ValueError(
                f"No active prompt found in database for agent_id='{agent_id}', prompt_key='{prompt_key}'. "
                f"Ensure a prompt with is_active=True exists."
            )
        return prompt
