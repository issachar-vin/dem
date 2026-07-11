"""Conductor-side pipeline events for a job — the steps the scheduler took (prep repo, opened PR,
review passed, failed) — recorded so the console can render a timeline interleaved with the job's
agent runs. Distinct from `agent_runs`, which captures the agent containers' own stdout."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import JobEvent


class JobEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    level: str
    message: str
    created_at: datetime


async def record_event(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
    message: str,
    ticket_id: str = "",
    level: str = "info",
) -> None:
    async with sessionmaker() as session:
        session.add(JobEvent(job_id=job_id, ticket_id=ticket_id, message=message, level=level))
        await session.commit()


async def events_for_job(
    sessionmaker: async_sessionmaker[AsyncSession], job_id: int
) -> list[JobEventView]:
    async with sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id)
                    .order_by(JobEvent.created_at.asc(), JobEvent.id.asc())
                )
            )
            .scalars()
            .all()
        )
        return [JobEventView.model_validate(row) for row in rows]
