"""No-network test: generations carry non-overlapping usage keys, OpenRouter cost, and thinking."""
from agents.business_analyst.observability import record_generation


class FakeObservation:
    def __init__(self):
        self.updates = {}

    def update(self, **kwargs):
        self.updates.update(kwargs)


def test_usage_is_not_double_counted_and_reasoning_is_kept() -> None:
    obs = FakeObservation()
    usage = {"prompt_tokens": 2883, "completion_tokens": 5416, "total_tokens": 8299, "cost": 0.0031,
             "prompt_tokens_details": {"cached_tokens": 800}}
    record_generation(obs, {"role": "assistant", "content": "{}"}, model="z-ai/glm-5.3-flash", usage=usage, reasoning="thinking")

    details = obs.updates["usage_details"]
    assert details == {"input": 2083, "output": 5416, "cache_read_input_tokens": 800}
    assert sum(details.values()) == 8299  # Langfuse derives total as the sum of usage keys
    assert obs.updates["cost_details"] == {"total": 0.0031}
    assert obs.updates["output"]["reasoning"] == "thinking"
    assert obs.updates["model"] == "z-ai/glm-5.3-flash"


def test_openrouter_calls_carry_prompt_cache_routing() -> None:
    from types import SimpleNamespace
    from agents.business_analyst.llm_client import with_prompt_cache
    from agents.business_analyst.observability import observation

    openrouter = SimpleNamespace(base_url="https://openrouter.ai/api/v1/")
    with observation("record-chat-message", session_id="project-1"):
        params = with_prompt_cache(openrouter, {"model": "m", "extra_body": {"reasoning": {"enabled": True}}}, "parse-project-context")
    assert params["extra_body"] == {
        "reasoning": {"enabled": True},
        "prompt_cache_key": "ba-parse-project-context-project-1",
        "session_id": "ba-project-1",
    }
    assert with_prompt_cache(SimpleNamespace(base_url="https://api.deepseek.com"), {"model": "m"}, "x") == {"model": "m"}
