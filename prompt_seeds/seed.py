"""Seed the Business Analyst OS prompt catalog."""

from sqlalchemy import select

from agents.business_analyst.registry import seed_all_ba_catalogs
from models.agent_prompt import AgentPrompt
from models.engine import get_async_session
from prompt_seeds.ba_prompts import PROMPTS as BA_PROMPTS


async def seed_ba_prompts_async() -> None:
    """Create missing BA prompts and the BA capability/deliverable catalogs."""
    async with get_async_session() as session:
        for prompt_key, prompt in BA_PROMPTS.items():
            existing = await session.execute(
                select(AgentPrompt.id).where(
                    AgentPrompt.agent_id == "9",
                    AgentPrompt.prompt_key == prompt_key,
                    AgentPrompt.version == "v1",
                )
            )
            if existing.scalar_one_or_none() is None:
                session.add(
                    AgentPrompt(
                        agent_id="9",
                        agent_name="BusinessAnalystAgent",
                        prompt_key=prompt_key,
                        prompt=prompt,
                        version="v1",
                        is_active=True,
                    )
                )
        await seed_all_ba_catalogs(session)


if __name__ == "__main__":
    import asyncio

    asyncio.run(seed_ba_prompts_async())
