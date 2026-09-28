"""Minimal BA-local structured-output LLM client (raw OpenAI SDK against OpenRouter by default).

The raw SDK is used instead of ChatOpenAI because ChatOpenAI drops OpenRouter's non-standard
`reasoning` field, which is what the analyst streams as thinking and what Langfuse records on
each generation (see observability.py).
"""

import json
import os
from typing import Any, Literal, Optional, Type, TypeVar

import openai
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

from agents.business_analyst.cost_log import current_session_id
from agents.business_analyst.observability import observation, record_error, record_generation

T = TypeVar("T", bound=BaseModel)

load_dotenv(override=False)

# Two tiers (product decision, 2026-09-25), each the other's fallback:
# - chat: gpt-6-luna with reasoning off — chat-turn extraction, summaries, question phrasing.
#   Measured ~11s on the semantic-planner prompt with valid JSON; $0.10/$0.50 per M tokens.
# - deep reasoning: mimo-v2.6-pro with reasoning on — the project-investigation agents
#   (project_harness). $0.435/$0.87 per M tokens; BA_LLM_REASONING_EFFORT (low|medium|high)
#   is its latency knob, unset leaves the provider default.
OPENROUTER_CHAT_MODEL = "typesafe/jev-router"
OPENROUTER_REASONING_MODEL = "xiaomi/mimo-v2.6-pro"
JEV_ROUTER_MODEL = "typesafe/jev-router"
REQUEST_TIMEOUT_SECONDS = 300
# Errors where the request itself is wrong for this model (bad id, bad key): retrying can't help.
_NON_RETRYABLE = (openai.BadRequestError, openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)


def _clean_json(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        return "\n".join(lines).strip()
    return content


LLMMode = Literal["chat", "reasoning", "evaluation"]


def llm_client_args(
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    *,
    reasoning: bool = False,
    mode: LLMMode | None = None,
) -> list[dict[str, Any]]:
    """Client and request settings for the primary model, then the fallback model.

    Modes select separate chat, analysis, and evaluation tiers. `reasoning=True` remains a
    compatibility alias for the reasoning mode. Overrides: BA_LLM_MODEL, BA_ANALYST_MODEL,
    BA_EVALUATOR_MODEL, BA_LLM_FALLBACK_MODEL. The generic LLM_MODEL is deliberately ignored:
    it names models for other stacks' providers (e.g. DeepSeek-direct ids), which OpenRouter rejects.
    """
    selected_mode = mode or ("reasoning" if reasoning else "chat")
    if selected_mode not in ("chat", "reasoning", "evaluation"):
        raise ValueError(f"Unsupported BA LLM mode: {selected_mode}")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    api_key = openrouter_key or deepseek_key or os.getenv("OPENAI_API_KEY") or os.getenv("NVIDIA_LLM_API_KEY")
    if not api_key:
        raise ValueError("Set OPENROUTER_API_KEY, DEEPSEEK_API_KEY, OPENAI_API_KEY, or NVIDIA_LLM_API_KEY before using BA LLM features.")
    base_url = os.getenv("BA_LLM_BASE_URL") or ("https://openrouter.ai/api/v1" if openrouter_key else "https://api.deepseek.com" if deepseek_key else os.getenv("OPENAI_BASE_URL") or os.getenv("NVIDIA_LLM_BASE_URL"))
    if openrouter_key:
        chat_model = os.getenv("BA_LLM_MODEL") or OPENROUTER_CHAT_MODEL
        reasoning_model = os.getenv("BA_ANALYST_MODEL") or OPENROUTER_REASONING_MODEL
        evaluator_model = os.getenv("BA_EVALUATOR_MODEL") or reasoning_model
        primary, other = {
            "chat": (chat_model, reasoning_model),
            "reasoning": (reasoning_model, chat_model),
            "evaluation": (evaluator_model, chat_model),
        }[selected_mode]
    else:
        chat_model = os.getenv("BA_LLM_MODEL") or ("deepseek-v4-pro" if deepseek_key else "gpt-6-luna")
        reasoning_model = os.getenv("BA_ANALYST_MODEL") or chat_model
        evaluator_model = os.getenv("BA_EVALUATOR_MODEL") or reasoning_model
        primary, other = {
            "chat": (chat_model, reasoning_model),
            "reasoning": (reasoning_model, chat_model),
            "evaluation": (evaluator_model, chat_model),
        }[selected_mode]
    primary = model or primary
    fallback = os.getenv("BA_LLM_FALLBACK_MODEL") or other
    configs: list[dict[str, Any]] = []
    for name in dict.fromkeys(filter(None, [primary, fallback])):
        args: dict[str, Any] = {"api_key": api_key, "model": name, "timeout": REQUEST_TIMEOUT_SECONDS, "max_retries": 1}
        if name.rsplit("/", 1)[-1].startswith("gpt-6-"):
            args["reasoning_effort"] = "none"
        elif name == JEV_ROUTER_MODEL:
            # Jev Router selects both the downstream model and reasoning effort itself.
            # Keep the normal sampling temperature but do not pin OpenRouter reasoning options.
            args["temperature"] = 0.1 if temperature is None else temperature
        else:
            args["temperature"] = 0.1 if temperature is None else temperature
            if openrouter_key:
                effort = os.getenv("BA_LLM_REASONING_EFFORT")
                args["extra_body"] = {"reasoning": {"enabled": True, **({"effort": effort} if effort else {})}}
        if base_url:
            args["base_url"] = base_url
        configs.append(args)
    return configs


def chat_client(args: dict[str, Any]) -> tuple[AsyncOpenAI, dict[str, Any]]:
    """Splits llm_client_args() output into an SDK client and per-request parameters."""
    client = AsyncOpenAI(api_key=args["api_key"], base_url=args.get("base_url"), timeout=args["timeout"], max_retries=args["max_retries"])
    params = {key: args[key] for key in ("model", "temperature", "reasoning_effort", "extra_body") if key in args}
    return client, params


def with_prompt_cache(client: AsyncOpenAI, params: dict[str, Any], name: str) -> dict[str, Any]:
    """Request params plus OpenRouter prompt-cache routing for this call.

    Provider prompt caching is automatic (OpenAI from 1,024 prompt tokens; MiMo too, measured
    27-55% cached). `prompt_cache_key` groups calls that share a prefix (same step, same project)
    and `session_id` pins them to the provider holding that cache (sticky routing expires after
    10 idle minutes): https://openrouter.ai/docs/features/prompt-caching. Callers keep each
    prompt's fixed part first and append new data last so the cached prefix stays valid.
    """
    if "openrouter.ai" not in str(getattr(client, "base_url", "")):
        return params
    project = current_session_id()
    key = f"ba-{name}-{project}" if project else f"ba-{name}"
    return {**params, "extra_body": {**params.get("extra_body", {}), "prompt_cache_key": key, "session_id": f"ba-{project or name}"}}


def model_parameters(params: dict[str, Any]) -> dict[str, Any]:
    """Flat request settings for the Langfuse generation (it doesn't accept nested dicts)."""
    flat = {key: params[key] for key in ("temperature", "reasoning_effort", "max_tokens") if key in params}
    reasoning = (params.get("extra_body") or {}).get("reasoning")
    if reasoning:
        flat["reasoning"] = reasoning.get("effort", "enabled")
    return flat


async def get_structured_output(
    system_prompt: str,
    user_prompt: str | list[Any],
    response_model: Type[T],
    max_retries: int = 2,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    name: str = "generate-structured-output",
    mode: LLMMode = "chat",
    **_: Any,
) -> T:
    """Request JSON matching ``response_model``; `name` labels the call's Langfuse generation."""
    schema = json.dumps(response_model.model_json_schema(), indent=2)
    system = f"{system_prompt}\n\nRespond only with JSON matching this schema:\n{schema}"
    user = user_prompt if isinstance(user_prompt, str) else str(user_prompt)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error: Exception | None = None
    for args in llm_client_args(model, temperature, mode=mode):
        client, params = chat_client(args)
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        for _attempt in range(max_retries):
            with observation(
                name, as_type="evaluator" if mode == "evaluation" else "generation", input=messages, model=params["model"],
                model_parameters=model_parameters(params), metadata={"response_model": response_model.__name__, "mode": mode},
            ) as generation:
                try:
                    response = await client.chat.completions.create(messages=messages, response_format={"type": "json_object"}, **with_prompt_cache(client, params, name))
                    message = response.choices[0].message
                    record_generation(
                        generation, {"role": "assistant", "content": message.content},
                        model=response.model or params["model"], usage=response.usage,
                        reasoning=getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None),
                    )
                    return response_model.model_validate(json.loads(_clean_json(message.content or "")))
                except _NON_RETRYABLE as exc:
                    record_error(generation, exc)
                    last_error = exc
                    break
                except Exception as exc:
                    record_error(generation, exc)
                    last_error = exc
    raise RuntimeError("BA LLM did not return a valid structured response.") from last_error
