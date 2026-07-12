from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import job_events


async def test_record_and_list_in_order(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await job_events.record_event(
        sessionmaker, job_id=1, message="prep", ticket_id="T-1"
    )
    await job_events.record_event(
        sessionmaker, job_id=1, message="opened PR", level="success", ticket_id="T-1"
    )
    await job_events.record_event(sessionmaker, job_id=2, message="other")

    events = await job_events.events_for_job(sessionmaker, 1)
    assert [(e.message, e.level) for e in events] == [
        ("prep", "info"),
        ("opened PR", "success"),
    ]
    # Scoped by job: job 2's event isn't returned for job 1.
    assert [e.message for e in await job_events.events_for_job(sessionmaker, 2)] == [
        "other"
    ]
