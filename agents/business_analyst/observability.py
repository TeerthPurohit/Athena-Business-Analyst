"""Langfuse tracing for the BA agent (Python SDK 4.15, https://langfuse.com/docs/observability).

Enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (free Hobby cloud tier or
self-hosted; LANGFUSE_BASE_URL picks the region). Unset keys = every helper here is a no-op, the
same convention as the repo's other optional external services.

Trace shape (https://langfuse.com/docs/observability/best-practices):
- one trace per unit of work: a chat turn, an investigation, a document upload;
  session_id = project id, so a project's whole conversation replays as one session;
- every model call is its own `generation` (model, usage, OpenRouter cost, and the model's
  reasoning in the output), nested under the `agent` / `span` step that made it;
- names are stable and verb-first; run-specific values go in metadata, never in names.

LLM calls are recorded as explicit generations rather than via the langfuse.openai drop-in because
the drop-in does not capture OpenRouter's streamed `reasoning`, which the guidance requires.
"""
import functools
import inspect
import os
import re
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, TypeVar

from dotenv import load_dotenv

from agents.business_analyst import cost_log

F = TypeVar("F", bound=Callable[..., Any])

load_dotenv(override=False)  # Langfuse reads its credentials at client creation

TRACING_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
    and os.getenv("LANGFUSE_TRACING_ENABLED", "true").lower() != "false"
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")


def _mask(*, data: Any, **_: Any) -> Any:
    """Redacts email addresses and phone numbers from everything sent to Langfuse."""
    if isinstance(data, str):
        return _PHONE.sub("[phone]", _EMAIL.sub("[email]", data))
    if isinstance(data, dict):
        return {key: _mask(data=value) for key, value in data.items()}
    if isinstance(data, list):
        return [_mask(data=item) for item in data]
    return data


if TRACING_ENABLED:
    from langfuse import Langfuse, propagate_attributes

    _client: Any = Langfuse(mask=_mask)
else:
    _client = None


class _NoopObservation:
    def update(self, **_: Any) -> "_NoopObservation":
        return self


@contextmanager
def observation(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: Optional[dict[str, Any]] = None,
    session_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    label: Optional[str] = None,
    cost_model: Optional[str] = None,
    **attributes: Any,
) -> Iterator[Any]:
    """Opens an observation as a child of the current one, or as the trace root if there is none.

    session_id / tags propagate to every observation created inside the block. Tags are only
    applied at creation time, so pass them on the root. Every observation is also a node in the
    run's cost ledger (cost_log.py); `label` names it there (e.g. with the file and part) while
    the Langfuse name stays stable. Generations and evaluators are model calls in the ledger;
    `cost_model` names the model for types Langfuse doesn't attach a model to (evaluators).
    """
    with cost_log.track(
        label or name, model_call=as_type in ("generation", "evaluator"),
        model=attributes.get("model") or cost_model, session_id=session_id,
    ):
        if _client is None:
            yield _NoopObservation()
            return
        with _client.start_as_current_observation(as_type=as_type, name=name, input=input, metadata=metadata, **attributes) as obs:
            if session_id or tags:
                with propagate_attributes(session_id=session_id, tags=tags):
                    yield obs
            else:
                yield obs


def traced(
    name: str,
    *,
    as_type: str = "span",
    tags: Optional[list[str]] = None,
    input: Optional[Callable[[dict[str, Any]], Any]] = None,
    output: Optional[Callable[[Any], Any]] = None,
    **attributes: Any,
) -> Callable[[F], F]:
    """Runs an async function inside `observation(name, ...)`.

    `input` maps the bound arguments to what the observation shows (never raw sessions, bytes,
    or credentials); `output` maps the return value. A BATenantContext-like argument (org_id +
    project_id) makes the project the session, so every trace joins its project's session.
    """
    def decorate(fn: F) -> F:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs).arguments
            ctx = next((value for value in bound.values() if hasattr(value, "org_id") and hasattr(value, "project_id")), None)
            with observation(
                name, as_type=as_type, input=input(bound) if input else None,
                session_id=getattr(ctx, "project_id", None), tags=tags,
                metadata={"org_id": ctx.org_id} if ctx is not None else None, **attributes,
            ) as obs:
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    record_error(obs, exc)
                    raise
                obs.update(output=output(result) if output else result)
                return result
        return wrapper  # type: ignore[return-value]
    return decorate


def record_generation(obs: Any, message: Any, *, model: Optional[str], usage: Any = None, reasoning: Optional[str] = None) -> None:
    """Writes an LLM call's result onto its generation (output, thinking, tokens, cost) and into
    the run's cost ledger."""
    output = dict(message) if isinstance(message, dict) else {"role": "assistant", "content": message}
    if reasoning:
        output["reasoning"] = reasoning
    update: dict[str, Any] = {"output": output, "model": model}
    usage_dict = usage.model_dump(exclude_none=True) if hasattr(usage, "model_dump") else usage
    if isinstance(usage_dict, dict):
        # Langfuse sums every usage key into `total`, so pass only non-overlapping keys: passing
        # OpenAI's total_tokens as well double-counted (audited trace, 2026-09-25).
        cached = (usage_dict.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        details = {
            "input": (usage_dict.get("prompt_tokens") or 0) - cached,
            "output": usage_dict.get("completion_tokens") or 0,
        }
        if cached:
            details["cache_read_input_tokens"] = cached
        update["usage_details"] = details
        cost = usage_dict.get("cost")  # OpenRouter reports the billed USD cost per call
        if isinstance(cost, (int, float)):
            update["cost_details"] = {"total": cost}
        cost_log.record_call(
            model=model, input_tokens=usage_dict.get("prompt_tokens"), output_tokens=usage_dict.get("completion_tokens"),
            cost=cost if isinstance(cost, (int, float)) else None,
        )
    else:
        cost_log.record_call(model=model)
    obs.update(**update)


def record_error(obs: Any, exc: BaseException) -> None:
    cost_log.record_call(failed=True)
    obs.update(level="ERROR", status_message=f"{type(exc).__name__}: {str(exc)[:500]}")
