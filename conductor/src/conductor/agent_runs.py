"""Persisted agent-run output. The agent containers stream their events to stdout and are removed on
exit, so the conductor captures each `claude -p` run here for the console to replay afterwards."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import AgentRunLog

# Keep the tail (which holds the final result event) so a runaway agent can't bloat the DB.
_MAX_OUTPUT_CHARS = 200_000


class AgentRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    loop_round: int
    ok: bool
    output: str
    created_at: datetime


async def record_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    ticket_id: str,
    role: str,
    loop_round: int,
    output: str,
    ok: bool,
) -> None:
    if len(output) > _MAX_OUTPUT_CHARS:
        output = "…[truncated]…\n" + output[-_MAX_OUTPUT_CHARS:]
    async with sessionmaker() as session:
        session.add(
            AgentRunLog(ticket_id=ticket_id, role=role, loop_round=loop_round, output=output, ok=ok)
        )
        await session.commit()


async def runs_for_ticket(
    sessionmaker: async_sessionmaker[AsyncSession], ticket_id: str
) -> list[AgentRunView]:
    async with sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(AgentRunLog)
                    .where(AgentRunLog.ticket_id == ticket_id)
                    .order_by(AgentRunLog.created_at.asc(), AgentRunLog.id.asc())
                )
            )
            .scalars()
            .all()
        )
        return [AgentRunView.model_validate(row) for row in rows]
