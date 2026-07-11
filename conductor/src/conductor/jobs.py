"""Job intake: the one place external triggers (Plane/GitHub webhooks, the poll loop) turn into
queued Jobs. Intake is deliberately dumb — it enqueues work; it does not decide pipeline
transitions. That keeps adding a pipeline step (e.g. a future "blocked" or "awaiting feedback"
state) a state-machine concern, never an intake concern."""

import logging
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import AgentRunLog, Job, JobStatus, Ticket

logger = logging.getLogger("conductor")

_ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class JobView(BaseModel):
    id: int
    source: str
    event_type: str
    status: str
    dedupe_key: str | None
    delivery_id: str | None
    payload: dict[str, Any]
    raw_payloads: list[dict[str, Any]]
    created_at: datetime


async def enqueue_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    source: str,
    event_type: str,
    payload: dict[str, Any],
    delivery_id: str | None = None,
    dedupe_key: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> Job | None:
    """Insert a Job, or return None if it is a duplicate.

    Two independent dedupe layers:
    - `delivery_id` (unique column) rejects a literal re-delivery of the same webhook event.
    - `dedupe_key` rejects a *semantically* duplicate trigger while a prior job for the same key
      is still active (queued/running) — e.g. a repeat Plane issue event for an in-flight issue.

    The semantic check here is a read-then-insert, so under truly concurrent enqueues (Plane can
    deliver the same issue event twice, milliseconds apart) both callers may pass the read. The
    `ix_jobs_active_dedupe` partial-unique index is the backstop: the losing insert raises
    IntegrityError and is dropped, so at most one active job per (source, dedupe_key) survives.

    `raw_payload` (the full provider body) is recorded on the job: it seeds a new job's
    `raw_payloads` and, when a delivery is deduped onto an existing active job, is appended to that
    job's list — so every delivery that folded into one job is inspectable on the Jobs page.
    """
    async with sessionmaker() as session:
        if dedupe_key is not None:
            existing = (
                await session.execute(
                    select(Job).where(
                        Job.source == source,
                        Job.dedupe_key == dedupe_key,
                        Job.status.in_(_ACTIVE_STATUSES),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if raw_payload is not None:
                    existing.raw_payloads = [*existing.raw_payloads, raw_payload]
                    await session.commit()
                return None

        job = Job(
            delivery_id=delivery_id,
            dedupe_key=dedupe_key,
            source=source,
            event_type=event_type,
            payload=payload,
            raw_payloads=[raw_payload] if raw_payload is not None else [],
        )
        session.add(job)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return None
    return job


async def claim_job(sessionmaker: async_sessionmaker[AsyncSession], job_id: int) -> bool:
    """Atomically move a job queued → running. Returns False if another worker already claimed it
    (the guard makes concurrent workers safe even though v1 runs a single serial worker)."""
    async with sessionmaker() as session:
        result = cast(
            CursorResult[Any],
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
                .values(status=JobStatus.RUNNING, attempts=Job.attempts + 1)
            ),
        )
        await session.commit()
        return result.rowcount == 1


async def requeue_running(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Reset orphaned RUNNING jobs back to QUEUED and return how many. Called on startup: if the
    conductor is restarted mid-dispatch, a job left RUNNING never completes on its own and — because
    the active-dedupe index counts RUNNING as active — also blocks any re-trigger of that issue."""
    async with sessionmaker() as session:
        result = cast(
            CursorResult[Any],
            await session.execute(
                update(Job).where(Job.status == JobStatus.RUNNING).values(status=JobStatus.QUEUED)
            ),
        )
        await session.commit()
        return result.rowcount


async def complete_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    status: JobStatus,
    error: str | None = None,
) -> None:
    # Compare-and-set on RUNNING: if the job was manually stopped from the console mid-run (which
    # kills the container and makes the scheduler try to fail it here), the stop wins — this write
    # matches no rows and is a no-op, so the `stopped` status is preserved.
    async with sessionmaker() as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.RUNNING)
            .values(status=status, error=error)
        )
        await session.commit()


async def get_job(sessionmaker: async_sessionmaker[AsyncSession], job_id: int) -> Job | None:
    async with sessionmaker() as session:
        return await session.get(Job, job_id)


async def stop_job(sessionmaker: async_sessionmaker[AsyncSession], job_id: int) -> Job | None:
    """Mark an active (queued/running) job `stopped` and return it (its payload names the containers
    to kill). Returns None if the job already reached a terminal state — nothing to stop."""
    async with sessionmaker() as session:
        result = cast(
            CursorResult[Any],
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)))
                .values(status=JobStatus.STOPPED)
            ),
        )
        await session.commit()
        return await session.get(Job, job_id) if result.rowcount == 1 else None


async def list_jobs(
    sessionmaker: async_sessionmaker[AsyncSession], *, limit: int = 200
) -> list[JobView]:
    async with sessionmaker() as session:
        rows = (
            (await session.execute(select(Job).order_by(Job.created_at.desc()).limit(limit)))
            .scalars()
            .all()
        )
        return [
            JobView(
                id=r.id,
                source=r.source,
                event_type=r.event_type,
                status=r.status,
                dedupe_key=r.dedupe_key,
                delivery_id=r.delivery_id,
                payload=r.payload,
                raw_payloads=r.raw_payloads,
                created_at=r.created_at,
            )
            for r in rows
        ]


async def delete_job(sessionmaker: async_sessionmaker[AsyncSession], job_id: int) -> bool:
    """Delete a job and every record it produced: its agent-run log history (scoped by job, so a
    re-triggered ticket's earlier runs are left alone) and, if the job drove a ticket, that ticket's
    pipeline-mirror row. There is no DB-level FK, so the cascade is done here explicitly."""
    async with sessionmaker() as session:
        row = await session.get(Job, job_id)
        if row is None:
            return False
        await session.execute(delete(AgentRunLog).where(AgentRunLog.job_id == job_id))
        ticket_id = str(row.payload.get("issue_id") or "")
        if ticket_id:
            await session.execute(delete(Ticket).where(Ticket.ticket_id == ticket_id))
        await session.delete(row)
        await session.commit()
        return True
