"""Persisted agent-run output. The agent containers stream their events to stdout and are removed on
exit, so the conductor captures each `claude -p` run here for the console to replay afterwards."""

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import AgentRunLog, AgentRunStatus

# Keep the tail (which holds the final result event) so a runaway agent can't bloat the DB.
_MAX_OUTPUT_CHARS = 200_000


def _cap(output: str) -> str:
    if len(output) <= _MAX_OUTPUT_CHARS:
        return output
    return "…[truncated]…\n" + output[-_MAX_OUTPUT_CHARS:]


class AgentRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    loop_round: int
    status: str
    ok: bool
    output: str
    created_at: datetime


async def start_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    ticket_id: str,
    role: str,
    loop_round: int,
) -> int:
    """Open a `running` run row before the container's first byte and return its id; the streamed
    output is appended to it as events arrive."""
    async with sessionmaker() as session:
        row = AgentRunLog(
            job_id=job_id,
            ticket_id=ticket_id,
            role=role,
            loop_round=loop_round,
            status=AgentRunStatus.RUNNING,
        )
        session.add(row)
        await session.commit()
        return row.id


async def append_output(
    sessionmaker: async_sessionmaker[AsyncSession], run_id: int, chunk: str
) -> None:
    async with sessionmaker() as session:
        row = await session.get(AgentRunLog, run_id)
        if row is None:
            return
        row.output = _cap(row.output + chunk)
        await session.commit()


async def finish_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    run_id: int,
    *,
    ok: bool,
    output: str | None = None,
) -> None:
    """Mark a run terminal. `output`, if given, is appended first (used for the failure logs a
    streamed run never saw on stdout)."""
    async with sessionmaker() as session:
        row = await session.get(AgentRunLog, run_id)
        if row is None:
            return
        if output:
            row.output = _cap(row.output + output)
        row.ok = ok
        row.status = AgentRunStatus.DONE if ok else AgentRunStatus.FAILED
        await session.commit()


async def record_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    ticket_id: str,
    role: str,
    loop_round: int,
    output: str,
    ok: bool,
    job_id: int | None = None,
) -> None:
    """Persist a finished run in one shot (non-streamed capture / tests)."""
    async with sessionmaker() as session:
        session.add(
            AgentRunLog(
                job_id=job_id,
                ticket_id=ticket_id,
                role=role,
                loop_round=loop_round,
                output=_cap(output),
                ok=ok,
                status=AgentRunStatus.DONE if ok else AgentRunStatus.FAILED,
            )
        )
        await session.commit()


class DbRunRecorder:
    """The dispatcher's `RunRecorder`, bound to the DB: the streaming lifecycle expressed over the
    `agent_runs` store. Injected so the dispatcher stays free of DB coupling."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def start(self, *, job_id: int, ticket_id: str, role: str, loop_round: int) -> int:
        return await start_run(
            self._sessionmaker,
            job_id=job_id,
            ticket_id=ticket_id,
            role=role,
            loop_round=loop_round,
        )

    async def append(self, run_id: int, chunk: str) -> None:
        await append_output(self._sessionmaker, run_id, chunk)

    async def finish(self, run_id: int, *, ok: bool, output: str | None = None) -> None:
        await finish_run(self._sessionmaker, run_id, ok=ok, output=output)


async def _runs_where(
    sessionmaker: async_sessionmaker[AsyncSession], clause: Any
) -> list[AgentRunView]:
    async with sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(AgentRunLog)
                    .where(clause)
                    .order_by(AgentRunLog.created_at.asc(), AgentRunLog.id.asc())
                )
            )
            .scalars()
            .all()
        )
        return [AgentRunView.model_validate(row) for row in rows]


async def runs_for_job(
    sessionmaker: async_sessionmaker[AsyncSession], job_id: int
) -> list[AgentRunView]:
    return await _runs_where(sessionmaker, AgentRunLog.job_id == job_id)


async def runs_for_ticket(
    sessionmaker: async_sessionmaker[AsyncSession], ticket_id: str
) -> list[AgentRunView]:
    return await _runs_where(sessionmaker, AgentRunLog.ticket_id == ticket_id)


class RunSummary(BaseModel):
    """A human-readable digest of one captured run: the final `result` event as an outcome indicator
    plus a plain-language transcript derived from the stream-json events."""

    outcome: str  # the result event's subtype (e.g. "success", "error_max_turns"); "" if none seen
    is_error: bool
    result_text: str  # the agent's final result message
    meta: str  # "12 turns · 45.3s · $0.3400", any subset present
    sentences: list[str]


def parse_events(output: str) -> list[dict[str, Any]]:
    """Parse the captured stdout (stream-json / JSONL) into events, wrapping any non-JSON line as
    `{"raw": …}` so a partial or failed run still renders."""
    events: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            events.append({"raw": line})
            continue
        events.append(parsed if isinstance(parsed, dict) else {"value": parsed})
    return events


def summarize_output(output: str) -> RunSummary:
    outcome = ""
    is_error = False
    result_text = ""
    meta = ""
    sentences: list[str] = []
    for event in parse_events(output):
        if event.get("type") == "result":
            outcome, is_error, result_text, meta = _result_indicator(event)
            continue
        sentences.extend(_event_sentences(event))
    return RunSummary(
        outcome=outcome,
        is_error=is_error,
        result_text=result_text,
        meta=meta,
        sentences=sentences,
    )


def _result_indicator(event: dict[str, Any]) -> tuple[str, bool, str, str]:
    outcome = str(event.get("subtype") or ("error" if event.get("is_error") else "success"))
    parts: list[str] = []
    turns = event.get("num_turns")
    if isinstance(turns, int):
        parts.append(f"{turns} turn{'s' if turns != 1 else ''}")
    duration = event.get("duration_ms")
    if isinstance(duration, int | float):
        parts.append(f"{duration / 1000:.1f}s")
    cost = event.get("total_cost_usd")
    if isinstance(cost, int | float):
        parts.append(f"${cost:.4f}")
    return (
        outcome,
        bool(event.get("is_error")),
        str(event.get("result") or "").strip(),
        " · ".join(parts),
    )


# Tool name → the input field that best names what it did; falls back to the first non-empty string.
_TOOL_ARG = {
    "Bash": "command",
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "Task": "description",
    "WebFetch": "url",
    "WebSearch": "query",
}


def _event_sentences(event: dict[str, Any]) -> list[str]:
    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "init":
        model = event.get("model")
        return [f"Session started on {model}." if model else "Session started."]
    if etype == "assistant":
        return _content_sentences(_message_content(event))
    if etype == "user":
        return _tool_result_sentences(_message_content(event))
    if "raw" in event:
        return [str(event["raw"])]
    return []


def _message_content(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _content_sentences(content: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            text = str(block.get("text") or "").strip()
            if text:
                out.append(text)
        elif btype == "tool_use":
            out.append(_tool_use_sentence(block))
    return out


def _tool_use_sentence(block: dict[str, Any]) -> str:
    name = str(block.get("name") or "a tool")
    tool_input = block.get("input")
    detail = ""
    if isinstance(tool_input, dict):
        key = _TOOL_ARG.get(name)
        value = tool_input.get(key) if key else None
        if not isinstance(value, str) or not value.strip():
            value = next((v for v in tool_input.values() if isinstance(v, str) and v.strip()), None)
        if isinstance(value, str) and value.strip():
            detail = _truncate(value.strip().splitlines()[0], 120)
    return f"Used {name}: {detail}" if detail else f"Used {name}."


def _tool_result_sentences(content: list[dict[str, Any]]) -> list[str]:
    # Successful tool results are noise in a transcript; only surface the ones that erred.
    return [
        "A tool call returned an error."
        for block in content
        if block.get("type") == "tool_result" and block.get("is_error")
    ]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
