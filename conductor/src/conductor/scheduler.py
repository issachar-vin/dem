"""The scheduler: the piece that finally *consumes* intake Jobs. On an interval it selects the
next engineer ticket to build — in-flight work first, then the oldest `ready_for_dev` — gated by
Plane blocking relationships, and dispatches one agent container per role (MAX_CONCURRENT_AGENTS).

Part 3 wires the consumer end-to-end for the **engineer** trigger with a placeholder prompt and
mirrors the pipeline state onto the Plane board (ready_for_dev → in_progress → in_review). The real
role prompts, the reviewer/QA loop, and PR creation are Phase 5. Other job triggers (planner, GitHub
PR events) are left queued for Phase 5 to handle."""

import asyncio
import contextlib
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import plane
from conductor.agents.dispatcher import AgentRun, Dispatcher
from conductor.agents.roles import AgentRole
from conductor.agents.volumes import VolumeManager
from conductor.jobs import claim_job, complete_job, requeue_running
from conductor.mappings import MappingStore
from conductor.models import Job, JobStatus, WorkflowState
from conductor.plane import PlaneClient
from conductor.store import ConfigStore
from conductor.tickets import TicketStore

logger = logging.getLogger("conductor")

_INTERVAL_SECONDS = 5.0
_ENGINEER_TRIGGER = "engineer"


class Scheduler:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        store: ConfigStore,
        mappings: MappingStore,
        tickets: TicketStore,
        dispatcher: Dispatcher,
        volumes: VolumeManager,
        plane_factory: Callable[[dict[str, str]], PlaneClient] = plane.client_from_resolved,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._store = store
        self._mappings = mappings
        self._tickets = tickets
        self._dispatcher = dispatcher
        self._volumes = volumes
        self._plane_factory = plane_factory

    async def tick(self) -> bool:
        """Select and run at most one ticket. Returns True if work was dispatched this tick."""
        job = await self._select_engineer_job()
        if job is None:
            return False
        if not await claim_job(self._sessionmaker, job.id):
            return False  # another worker won the race
        await self._run_engineer(job)
        return True

    async def _select_engineer_job(self) -> Job | None:
        async with self._sessionmaker() as session:
            jobs = (
                (
                    await session.execute(
                        select(Job)
                        .where(Job.source == "plane", Job.status == JobStatus.QUEUED)
                        .order_by(Job.created_at.asc(), Job.id.asc())
                    )
                )
                .scalars()
                .all()
            )
        in_flight = await self._tickets.in_flight_ids()
        candidates: list[Job] = []
        for job in jobs:
            if job.payload.get("trigger") != _ENGINEER_TRIGGER:
                continue  # planner / other triggers are Phase 5
            if await self._is_blocked(job):
                continue
            candidates.append(job)
        # In-flight tickets outrank fresh ones; oldest-created breaks ties (already sorted asc).
        candidates.sort(key=lambda j: 0 if str(j.payload.get("issue_id")) in in_flight else 1)
        return candidates[0] if candidates else None

    async def _is_blocked(self, job: Job) -> bool:
        """Plane blocking-relationship eligibility gate. Phase 5 seam: the planner does not create
        blocking relationships until Phase 5, and the CE relations endpoint must be validated live
        before we call it — so nothing is blocked yet."""
        return False

    async def _run_engineer(self, job: Job) -> None:
        project_id = str(job.payload.get("project_id", ""))
        issue_id = str(job.payload.get("issue_id", ""))
        try:
            repo, base_branch = await self._resolve_repo(project_id)
            cfg = await self._store.resolved()
            plane_client = self._plane_factory(cfg)
            await self._tickets.get_or_create(issue_id, project_id)
            await self._set_state(
                plane_client, project_id, issue_id, "in_progress", WorkflowState.IN_PROGRESS
            )
            await self._volumes.prepare(
                ticket_id=issue_id, github_repo=repo, base_branch=base_branch
            )
            envelope = await self._dispatcher.run(
                AgentRun(
                    role=AgentRole.ENGINEER,
                    ticket_id=issue_id,
                    prompt=_placeholder_prompt(issue_id),
                    model=cfg.get("claude_model_engineer", "claude-sonnet-4-6"),
                )
            )
            await self._tickets.set_engineer_session(issue_id, envelope.session_id)
            await self._set_state(
                plane_client, project_id, issue_id, "in_review", WorkflowState.IN_REVIEW
            )
            await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
            logger.info("Engineer built ticket %s (session %s)", issue_id, envelope.session_id)
        except Exception as exc:
            await complete_job(self._sessionmaker, job.id, status=JobStatus.FAILED, error=str(exc))
            if issue_id:
                await self._tickets.set_status(issue_id, "error")
            logger.exception("Engineer dispatch failed for ticket %s", issue_id)

    async def _set_state(
        self,
        plane_client: PlaneClient,
        project_id: str,
        issue_id: str,
        local_status: str,
        workflow_state: WorkflowState,
    ) -> None:
        """Advance the ticket's local status and mirror it onto the Plane board. Best-effort: an
        unmapped state or a Plane error is logged, and the dispatch still continues."""
        await self._tickets.set_status(issue_id, local_status)
        state_id = await self._mappings.get_state_id(project_id, workflow_state)
        if state_id is None:
            logger.info(
                "No Plane state mapped for %s (project %s); board not moved",
                workflow_state,
                project_id,
            )
            return
        try:
            await plane_client.set_state(project_id, issue_id, state_id)
        except plane.PlaneError as exc:
            logger.warning(
                "Could not move issue %s to %s in Plane: %s", issue_id, workflow_state, exc.detail
            )

    async def _resolve_repo(self, project_id: str) -> tuple[str, str]:
        """The target repo for a ticket. Phase 5's planner assigns each ticket one repo key; until
        then (human-created ready_for_dev tickets) take the project's first mapped repo."""
        repos = await self._mappings.list_repos(project_id)
        if not repos:
            raise RuntimeError(f"project {project_id} has no mapped repo")
        repo = repos[0]
        return repo.github_repo, repo.base_branch

    async def recover(self) -> None:
        """Requeue jobs left RUNNING by a previous process (e.g. a redeploy mid-dispatch)."""
        requeued = await requeue_running(self._sessionmaker)
        if requeued:
            logger.warning("Requeued %d orphaned running job(s) from a prior run", requeued)

    async def run(self) -> None:
        while True:
            try:
                worked = await self.tick()
                if worked:
                    continue  # drain the queue without waiting between ready tickets
            except Exception:  # a scheduling error must not kill the loop
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(_INTERVAL_SECONDS)


def _placeholder_prompt(issue_id: str) -> str:
    # Phase 5 replaces this with the real engineer prompt (ticket body + criteria + findings on
    # resume). For now we only need a run that produces a session id to prove the consumer.
    return (
        f"You are a placeholder engineer agent for ticket {issue_id}. "
        "Reply with a one-sentence acknowledgement; do not modify any files."
    )


async def start(scheduler: Scheduler) -> asyncio.Task[None]:
    await scheduler.recover()
    task = asyncio.create_task(scheduler.run())
    logger.info("Scheduler started")
    return task


async def stop(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
