"""No-network test: a run's model calls are costed per step and part, and totalled in log.md."""
import asyncio

from agents.business_analyst.observability import observation, record_error, record_generation


def test_run_writes_node_wise_costs_part_subtotals_and_total(tmp_path, monkeypatch) -> None:
    log = tmp_path / "log.md"
    monkeypatch.setenv("BA_COST_LOG_PATH", str(log))

    async def model_call(step, model, tokens_in, tokens_out, cost, fail=False):
        with observation(step, as_type="generation", model=model) as gen:
            await asyncio.sleep(0.01)
            if fail:
                record_error(gen, RuntimeError("provider down"))
                return
            record_generation(gen, {"role": "assistant", "content": "ok"}, model=model,
                              usage={"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "cost": cost})

    async def run():
        with observation("investigate-project", as_type="agent", session_id="project-1"):
            await model_call("decide-next-step", "xiaomi/mimo-v2.6-pro", 1000, 200, 0.002)
            with observation("scope-analyst", as_type="agent"):
                # Concurrent calls in one part still land in the same run and part.
                await asyncio.gather(
                    model_call("decide-next-step", "xiaomi/mimo-v2.6-pro", 500, 100, 0.001),
                    model_call("decide-next-step", "openai/gpt-6-luna", 100, 10, None, fail=True),
                )
            with observation("choose-inspection-tool", as_type="generation", model="typesafe/jev-1.13"):
                pass  # a model call whose provider reports no cost

    asyncio.run(run())
    text = log.read_text(encoding="utf-8")

    assert text.startswith("# BA model cost log")
    assert "investigate-project · project `project-1`" in text
    assert "**Total: $0.003000** · 4 model calls (2 unpriced" in text
    assert "| scope-analyst | decide-next-step (failed) | openai/gpt-6-luna |" in text
    assert "| scope-analyst | 2 | 0.001000 | 33% |" in text
    assert "| investigate-project | 2 | 0.002000 | 67% |" in text
    assert text.count("## ") == 1  # nested observations never start their own run
