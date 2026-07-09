import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.jobs import enqueue_job
from conductor.models import Job, JobStatus


async def test_active_dedupe_backstop_blocks_concurrent_duplicate(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # The concurrent-delivery race: two enqueues both pass enqueue_job's read-check, so the guard
    # must be at the DB. A second *active* job with the same (source, dedupe_key) is rejected.
    async with sessionmaker() as session:
        session.add(Job(source="plane", event_type="e", payload={}, dedupe_key="p:i"))
        await session.commit()
    with pytest.raises(IntegrityError):
        async with sessionmaker() as session:
            session.add(
                Job(source="plane", event_type="e", payload={}, dedupe_key="p:i")
            )
            await session.commit()


async def test_active_dedupe_backstop_allows_other_source(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    for source in ("plane", "github"):
        async with sessionmaker() as session:
            session.add(
                Job(source=source, event_type="e", payload={}, dedupe_key="p:i")
            )
            await session.commit()
    async with sessionmaker() as session:
        assert len((await session.execute(select(Job))).scalars().all()) == 2


async def test_enqueue_returns_job(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    job = await enqueue_job(
        sessionmaker, source="plane", event_type="issue.created", payload={"a": 1}
    )
    assert job is not None
    assert job.id is not None


async def test_delivery_id_dedupe(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, delivery_id="d1"
    )
    second = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, delivery_id="d1"
    )
    assert first is not None
    assert second is None


async def test_semantic_dedupe_while_active(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, dedupe_key="p:i"
    )
    second = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, dedupe_key="p:i"
    )
    assert first is not None
    assert second is None


async def test_semantic_dedupe_allows_new_after_terminal(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, dedupe_key="p:i"
    )
    assert first is not None
    async with sessionmaker() as session:
        row = await session.get(Job, first.id)
        assert row is not None
        row.status = JobStatus.DONE
        await session.commit()

    second = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, dedupe_key="p:i"
    )
    assert second is not None
    async with sessionmaker() as session:
        assert len((await session.execute(select(Job))).scalars().all()) == 2


async def test_semantic_dedupe_scoped_by_source(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    plane_job = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, dedupe_key="p:i"
    )
    github_job = await enqueue_job(
        sessionmaker, source="github", event_type="e", payload={}, dedupe_key="p:i"
    )
    assert plane_job is not None
    assert github_job is not None
