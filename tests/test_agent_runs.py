import json

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import agent_runs


async def test_record_and_list_in_order(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await agent_runs.record_run(
        sessionmaker,
        ticket_id="T-1",
        role="engineer",
        loop_round=0,
        output="build",
        ok=True,
    )
    await agent_runs.record_run(
        sessionmaker,
        ticket_id="T-1",
        role="reviewer",
        loop_round=0,
        output="review",
        ok=False,
    )
    await agent_runs.record_run(
        sessionmaker,
        ticket_id="T-2",
        role="engineer",
        loop_round=0,
        output="other",
        ok=True,
    )

    runs = await agent_runs.runs_for_ticket(sessionmaker, "T-1")
    assert [(r.role, r.ok) for r in runs] == [("engineer", True), ("reviewer", False)]
    assert runs[0].output == "build"


async def test_output_is_tail_capped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    big = "x" * (agent_runs._MAX_OUTPUT_CHARS + 5_000)
    await agent_runs.record_run(
        sessionmaker,
        ticket_id="T-3",
        role="engineer",
        loop_round=0,
        output=big,
        ok=True,
    )
    (run,) = await agent_runs.runs_for_ticket(sessionmaker, "T-3")
    assert (
        len(run.output) <= agent_runs._MAX_OUTPUT_CHARS + 20
    )  # tail kept + truncation marker
    assert run.output.startswith("…[truncated]…")


async def test_streaming_lifecycle_status_and_scoping(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await agent_runs.start_run(
        sessionmaker, job_id=5, ticket_id="T-9", role="engineer", loop_round=0
    )
    (live,) = await agent_runs.runs_for_job(sessionmaker, 5)
    assert live.status == "running" and live.output == ""

    await agent_runs.append_output(sessionmaker, run_id, "line1\n")
    await agent_runs.append_output(sessionmaker, run_id, "line2\n")
    await agent_runs.finish_run(sessionmaker, run_id, ok=True)

    (done,) = await agent_runs.runs_for_job(sessionmaker, 5)
    assert done.status == "done" and done.ok is True
    assert done.output == "line1\nline2\n"
    # A run for a different job is not returned here.
    assert await agent_runs.runs_for_job(sessionmaker, 6) == []


async def test_finish_failure_appends_logs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await agent_runs.start_run(
        sessionmaker, job_id=1, ticket_id="T-1", role="engineer", loop_round=0
    )
    await agent_runs.append_output(sessionmaker, run_id, "partial")
    await agent_runs.finish_run(sessionmaker, run_id, ok=False, output="\nboom")
    (run,) = await agent_runs.runs_for_job(sessionmaker, 1)
    assert run.status == "failed" and run.ok is False
    assert run.output == "partial\nboom"


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


def test_summarize_derives_sentences_and_result_indicator() -> None:
    output = _stream(
        {"type": "system", "subtype": "init", "model": "claude-opus-4-8"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me check the tests."},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "pytest -q\nmore"},
                    },
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "All done.",
            "num_turns": 3,
            "duration_ms": 45300,
            "total_cost_usd": 0.34,
        },
    )
    summary = agent_runs.summarize_output(output)
    assert summary.outcome == "success"
    assert summary.is_error is False
    assert summary.result_text == "All done."
    assert summary.meta == "3 turns · 45.3s · $0.3400"
    assert summary.sentences == [
        "Session started on claude-opus-4-8.",
        "Let me check the tests.",
        "Used Bash: pytest -q",  # first line only, tool arg picked by name
    ]


def test_summarize_flags_error_result() -> None:
    output = _stream(
        {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""}
    )
    summary = agent_runs.summarize_output(output)
    assert summary.outcome == "error_max_turns"
    assert summary.is_error is True


def test_summarize_handles_non_json_lines() -> None:
    summary = agent_runs.summarize_output("boom: traceback\nnot json")
    assert summary.sentences == ["boom: traceback", "not json"]
    assert summary.outcome == ""  # no result event captured


def test_parse_events_wraps_bad_lines() -> None:
    events = agent_runs.parse_events('{"type": "system"}\noops')
    assert events == [{"type": "system"}, {"raw": "oops"}]
