"""Per-run model cost ledger, appended to log.md (path: BA_COST_LOG_PATH; empty disables).

A run is one root observation: a chat turn, an investigation, an upload, a deliverable. Every
model call inside it is recorded with the part of the run it belongs to (its observation path),
the model, tokens, USD cost as billed by OpenRouter, when it started (seconds into the run), and
how long it took. When the run's root finishes, one Markdown section is appended: every call in
start order, a subtotal per part, and the run total. Works whether or not Langfuse is enabled;
observability.observation() drives it.
"""
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_LOG_PATH = Path(__file__).resolve().parents[2] / "log.md"


@dataclass
class _Call:
    part: str
    step: str
    model: Optional[str]
    at: float
    seconds: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost: Optional[float] = None
    failed: bool = False


@dataclass
class _Run:
    name: str
    session_id: Optional[str]
    started_at: datetime
    t0: float
    calls: list[_Call] = field(default_factory=list)


_run: ContextVar[Optional[_Run]] = ContextVar("ba_cost_run", default=None)
_path: ContextVar[tuple[str, ...]] = ContextVar("ba_cost_path", default=())
_call: ContextVar[Optional[_Call]] = ContextVar("ba_cost_call", default=None)
_write_lock = threading.Lock()


@contextmanager
def track(name: str, *, model_call: bool, model: Optional[str] = None, session_id: Optional[str] = None) -> Iterator[None]:
    """Enters one node of the current run (starting a run if there is none)."""
    run = _run.get()
    root_token = None
    if run is None:
        run = _Run(name, session_id, datetime.now(timezone.utc), time.perf_counter())
        root_token = _run.set(run)
    path = _path.get() + (name,)
    path_token = _path.set(path)
    call = call_token = None
    if model_call:
        call = _Call(part=" › ".join(path[1:-1]) or path[0], step=path[-1], model=model, at=time.perf_counter() - run.t0)
        call_token = _call.set(call)
    started = time.perf_counter()
    try:
        yield
    except BaseException:
        if call is not None:
            call.failed = True
        raise
    finally:
        if call is not None:
            call.seconds = time.perf_counter() - started
            run.calls.append(call)
            _call.reset(call_token)
        _path.reset(path_token)
        if root_token is not None:
            _run.reset(root_token)
            _write(run, time.perf_counter() - run.t0)


def current_session_id() -> Optional[str]:
    """The project of the run in progress (its root observation's session), if any."""
    run = _run.get()
    return run.session_id if run else None


def record_call(
    *, model: Optional[str] = None, input_tokens: Optional[int] = None, output_tokens: Optional[int] = None,
    cost: Optional[float] = None, failed: bool = False,
) -> None:
    """Fills in the model call currently being tracked (no-op outside a model call)."""
    call = _call.get()
    if call is None:
        return
    call.model = model or call.model
    call.input_tokens = input_tokens if input_tokens is not None else call.input_tokens
    call.output_tokens = output_tokens if output_tokens is not None else call.output_tokens
    call.cost = cost if cost is not None else call.cost
    call.failed = call.failed or failed


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def _render(run: _Run, seconds: float) -> str:
    priced = [call.cost for call in run.calls if call.cost is not None]
    total = sum(priced)
    unpriced = len(run.calls) - len(priced)
    lines = [
        f"## {run.started_at:%Y-%m-%d %H:%M:%S} UTC · {run.name}" + (f" · project `{run.session_id}`" if run.session_id else ""),
        "",
        f"**Total: ${total:.6f}** · {len(run.calls)} model calls"
        + (f" ({unpriced} unpriced: provider reported no cost)" if unpriced else "") + f" · {seconds:.1f}s wall time",
        "",
        "| At (s) | Part | Step | Model | In tokens | Out tokens | Cost (USD) | Took (s) |",
        "|---:|---|---|---|---:|---:|---:|---:|",
    ]
    for call in sorted(run.calls, key=lambda item: item.at):
        lines.append(
            f"| {call.at:.1f} | {_cell(call.part)} | {_cell(call.step)}{' (failed)' if call.failed else ''} | {_cell(call.model or '—')} "
            f"| {call.input_tokens if call.input_tokens is not None else '—'} | {call.output_tokens if call.output_tokens is not None else '—'} "
            f"| {f'{call.cost:.6f}' if call.cost is not None else '—'} | {call.seconds:.1f} |"
        )
    by_part: dict[str, list[float]] = {}
    for call in run.calls:
        totals = by_part.setdefault(call.part, [0.0, 0, 0.0])
        totals[0] += call.cost or 0.0
        totals[1] += 1
        totals[2] += call.seconds
    lines += ["", "| Part | Calls | Cost (USD) | Share | Model time (s) |", "|---|---:|---:|---:|---:|"]
    for part, (cost, count, model_seconds) in sorted(by_part.items(), key=lambda item: -item[1][0]):
        share = f"{cost / total:.0%}" if total else "—"
        lines.append(f"| {_cell(part)} | {int(count)} | {cost:.6f} | {share} | {model_seconds:.1f} |")
    lines.append("")
    lines.append("_Model time can exceed wall time: parts run concurrently._" if len(run.calls) > 1 else "")
    return "\n".join(lines).rstrip() + "\n\n"


def _write(run: _Run, seconds: float) -> None:
    path = os.environ.get("BA_COST_LOG_PATH", str(DEFAULT_LOG_PATH))
    if not path or not run.calls:
        return
    text = _render(run, seconds)
    # ponytail: a synchronous append of a few KB per run on the event loop, behind a process
    # lock; move to a queue or a DB table if many runs finish at once or workers share a volume.
    with _write_lock:
        target = Path(path)
        header = "" if target.exists() else "# BA model cost log\n\nOne section per run, appended when the run finishes.\n\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(header + text)
