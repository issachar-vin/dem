"""Job intake: the one place external triggers (Plane/GitHub webhooks, the poll loop) turn into
queued Jobs. Intake is deliberately dumb — it enqueues work; it does not decide pipeline
transitions. That keeps adding a pipeline step (e.g. a future "blocked" or "awaiting feedback"
state) a state-machine concern, never an intake concern."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import Job, JobStatus

logger = logging.getLogger("conductor")

_ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


async def enqueue_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    source: str,
    event_type: str,
    payload: dict[str, Any],
    delivery_id: str | None = None,
    dedupe_key: str | None = None,
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
    """
    async with sessionmaker() as session:
        if dedupe_key is not None:
            existing = (
                await session.execute(
                    select(Job.id).where(
                        Job.source == source,
                        Job.dedupe_key == dedupe_key,
                        Job.status.in_(_ACTIVE_STATUSES),
                    )
                )
            ).first()
            if existing is not None:
                return None

        job = Job(
            delivery_id=delivery_id,
            dedupe_key=dedupe_key,
            source=source,
            event_type=event_type,
            payload=payload,
        )
        session.add(job)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return None
    return job
