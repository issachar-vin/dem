"""Conductor-side ticket state. The Plane board stays the human source of truth; this table is the
durable local mirror the scheduler drives. `agent_status` holds the pipeline state the scheduler
cares about (Phase 5 will also push it to Plane custom properties)."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import Ticket

# Tickets past the ready_for_dev gate — the scheduler finishes these before pulling a new ticket.
IN_FLIGHT_STATUSES = frozenset({"in_progress", "in_review", "changes_requested"})


class TicketStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get_or_create(self, ticket_id: str, project_id: str) -> None:
        async with self._sessionmaker() as session:
            if await session.get(Ticket, ticket_id) is None:
                session.add(Ticket(ticket_id=ticket_id, project_id=project_id))
                await session.commit()

    async def set_status(self, ticket_id: str, status: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                update(Ticket).where(Ticket.ticket_id == ticket_id).values(agent_status=status)
            )
            await session.commit()

    async def set_pr(self, ticket_id: str, number: int, url: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                update(Ticket)
                .where(Ticket.ticket_id == ticket_id)
                .values(pr_number=number, pr_url=url)
            )
            await session.commit()

    async def bump_loop_round(self, ticket_id: str) -> int:
        async with self._sessionmaker() as session:
            ticket = await session.get(Ticket, ticket_id)
            assert ticket is not None  # the scheduler creates the ticket before the loop runs
            ticket.loop_round += 1
            await session.commit()
            return ticket.loop_round

    async def set_diff_hash(self, ticket_id: str, diff_hash: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                update(Ticket).where(Ticket.ticket_id == ticket_id).values(last_diff_hash=diff_hash)
            )
            await session.commit()

    async def set_engineer_session(self, ticket_id: str, session_id: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                update(Ticket)
                .where(Ticket.ticket_id == ticket_id)
                .values(engineer_session_id=session_id)
            )
            await session.commit()

    async def in_flight_ids(self) -> set[str]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Ticket.ticket_id).where(Ticket.agent_status.in_(IN_FLIGHT_STATUSES))
                )
            ).scalars()
            return set(rows)
