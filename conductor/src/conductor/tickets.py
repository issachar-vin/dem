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

    async def create_planned(
        self, ticket_id: str, project_id: str, *, target_repo: str | None, blocked_by: list[str]
    ) -> None:
        """Pre-create a planner ticket carrying its target repo and blocking graph, so the engineer
        job the ready_for_dev webhook later fires finds them already set (get_or_create is a no-op
        once the row exists)."""
        async with self._sessionmaker() as session:
            if await session.get(Ticket, ticket_id) is None:
                session.add(
                    Ticket(
                        ticket_id=ticket_id,
                        project_id=project_id,
                        agent_status="ready_for_dev",
                        target_repo=target_repo,
                        blocked_by=blocked_by,
                    )
                )
                await session.commit()

    async def get(self, ticket_id: str) -> Ticket | None:
        async with self._sessionmaker() as session:
            return await session.get(Ticket, ticket_id)

    async def get_by_pr_url(self, pr_url: str) -> Ticket | None:
        """The ticket a PR belongs to, matched on the full PR url so cross-repo PR-number collisions
        within a project can't clean up the wrong ticket."""
        async with self._sessionmaker() as session:
            return (
                await session.execute(select(Ticket).where(Ticket.pr_url == pr_url))
            ).scalar_one_or_none()

    async def statuses_for(self, ticket_ids: list[str]) -> dict[str, str]:
        if not ticket_ids:
            return {}
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(Ticket.ticket_id, Ticket.agent_status).where(
                        Ticket.ticket_id.in_(ticket_ids)
                    )
                )
            ).all()
            return {ticket_id: status for ticket_id, status in rows}

    async def set_status(self, ticket_id: str, status: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                update(Ticket).where(Ticket.ticket_id == ticket_id).values(agent_status=status)
            )
            await session.commit()

    async def set_status_unless(
        self, ticket_id: str, status: str, protected: tuple[str, ...]
    ) -> None:
        """Set the status unless the ticket is already in a protected state — used so the failure
        handler doesn't clobber a `stopped` ticket with `error` when a manual stop killed its
        container."""
        async with self._sessionmaker() as session:
            await session.execute(
                update(Ticket)
                .where(Ticket.ticket_id == ticket_id, Ticket.agent_status.not_in(protected))
                .values(agent_status=status)
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
