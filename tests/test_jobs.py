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


async def test_raw_payload_seeded_on_new_job(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    job = await enqueue_job(
        sessionmaker,
        source="plane",
        event_type="issue.updated",
        payload={},
        dedupe_key="p:i",
        raw_payload={"event": "issue", "n": 1},
    )
    assert job is not None
    async with sessionmaker() as session:
        row = await session.get(Job, job.id)
        assert row is not None
        assert row.raw_payloads == [{"event": "issue", "n": 1}]


async def test_deduped_delivery_appends_raw_payload(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first = await enqueue_job(
        sessionmaker,
        source="plane",
        event_type="issue.updated",
        payload={},
        dedupe_key="p:i",
        raw_payload={"delivery": 1},
    )
    assert first is not None
    # A second delivery for the same in-flight issue: no new job, but its payload is recorded.
    second = await enqueue_job(
        sessionmaker,
        source="plane",
        event_type="issue.updated",
        payload={},
        dedupe_key="p:i",
        raw_payload={"delivery": 2},
    )
    assert second is None
    async with sessionmaker() as session:
        row = await session.get(Job, first.id)
        assert row is not None
        assert row.raw_payloads == [{"delivery": 1}, {"delivery": 2}]


async def test_list_and_delete_jobs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from conductor.jobs import delete_job, list_jobs

    a = await enqueue_job(
        sessionmaker, source="plane", event_type="e", payload={}, dedupe_key="a"
    )
    await enqueue_job(sessionmaker, source="github", event_type="e", payload={})
    assert a is not None

    jobs = await list_jobs(sessionmaker)
    assert len(jobs) == 2
    assert {j.source for j in jobs} == {"plane", "github"}

    assert await delete_job(sessionmaker, a.id) is True
    assert await delete_job(sessionmaker, a.id) is False
    assert len(await list_jobs(sessionmaker)) == 1


async def test_delete_job_cascades_ticket_records(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from conductor import agent_runs
    from conductor.jobs import delete_job
    from conductor.models import AgentRunLog, Ticket

    async with sessionmaker() as session:
        job = Job(source="plane", event_type="e", payload={"issue_id": "T-1"})
        session.add_all(
            [
                job,
                Ticket(ticket_id="T-1", project_id="P"),
                Ticket(ticket_id="T-2", project_id="P"),
            ]
        )
        await session.commit()
        job_id = job.id
    await agent_runs.record_run(
        sessionmaker,
        ticket_id="T-1",
        role="engineer",
        loop_round=0,
        output="a",
        ok=True,
    )
    await agent_runs.record_run(
        sessionmaker,
        ticket_id="T-2",
        role="engineer",
        loop_round=0,
        output="b",
        ok=True,
    )

    assert await delete_job(sessionmaker, job_id) is True

    async with sessionmaker() as session:
        assert await session.get(Ticket, "T-1") is None
        assert (
            await session.get(Ticket, "T-2") is not None
        )  # unrelated ticket untouched
        remaining = (await session.execute(select(AgentRunLog))).scalars().all()
        assert [r.ticket_id for r in remaining] == ["T-2"]


async def test_delete_job_without_issue_id_leaves_records(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from conductor.jobs import delete_job

    async with sessionmaker() as session:
        job = Job(source="github", event_type="e", payload={})
        session.add(job)
        await session.commit()
        job_id = job.id
    assert await delete_job(sessionmaker, job_id) is True


async def test_stop_job_marks_active_job_stopped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from conductor.jobs import claim_job, stop_job

    async with sessionmaker() as session:
        job = Job(source="plane", event_type="e", payload={"issue_id": "I1"})
        session.add(job)
        await session.commit()
        job_id = job.id
    await claim_job(sessionmaker, job_id)  # queued → running

    stopped = await stop_job(sessionmaker, job_id)
    assert stopped is not None and stopped.status == JobStatus.STOPPED
    assert stopped.payload["issue_id"] == "I1"  # payload available for container kill


async def test_stop_job_none_when_already_terminal(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from conductor.jobs import complete_job, claim_job, stop_job

    async with sessionmaker() as session:
        job = Job(source="plane", event_type="e", payload={})
        session.add(job)
        await session.commit()
        job_id = job.id
    await claim_job(sessionmaker, job_id)
    await complete_job(sessionmaker, job_id, status=JobStatus.DONE)

    assert (
        await stop_job(sessionmaker, job_id) is None
    )  # already done → nothing to stop


async def test_complete_job_does_not_override_stopped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from conductor.jobs import complete_job, claim_job, stop_job

    async with sessionmaker() as session:
        job = Job(source="plane", event_type="e", payload={})
        session.add(job)
        await session.commit()
        job_id = job.id
    await claim_job(sessionmaker, job_id)
    await stop_job(sessionmaker, job_id)  # running → stopped (console stop)

    # The scheduler's container was killed by the stop, so it now tries to fail the job — must no-op.
    await complete_job(sessionmaker, job_id, status=JobStatus.FAILED, error="killed")
    async with sessionmaker() as session:
        assert (await session.get(Job, job_id)).status == JobStatus.STOPPED
