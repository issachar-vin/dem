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
