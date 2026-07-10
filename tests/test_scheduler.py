from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.agents.contracts import ClaudeEnvelope
from conductor.agents.dispatcher import AgentRun
from conductor.github import PullRequest
from conductor.jobs import enqueue_job
from conductor.mappings import MappingStore
from conductor.models import Job, Ticket, WorkflowState
from conductor.plane import PlaneError
from conductor.scheduler import Scheduler
from conductor.store import ConfigStore
from conductor.tickets import TicketStore


class FakeDispatcher:
    def __init__(
        self, *, session_id: str = "sess-1", error: Exception | None = None
    ) -> None:
        self._session_id = session_id
        self._error = error
        self.runs: list[AgentRun] = []

    async def run(self, run: AgentRun) -> ClaudeEnvelope:
        self.runs.append(run)
        if self._error is not None:
            raise self._error
        return ClaudeEnvelope(session_id=self._session_id, result="ok")


class FakeVolumes:
    def __init__(self) -> None:
        self.prepared: list[dict[str, str]] = []
        self.pushed: list[dict[str, str]] = []

    async def prepare(
        self, *, ticket_id: str, github_repo: str, base_branch: str
    ) -> None:
        self.prepared.append(
            {
                "ticket_id": ticket_id,
                "github_repo": github_repo,
                "base_branch": base_branch,
            }
        )

    async def push(self, *, ticket_id: str, github_repo: str) -> None:
        self.pushed.append({"ticket_id": ticket_id, "github_repo": github_repo})


class FakePlane:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.moves: list[tuple[str, str, str]] = []

    async def get_issue(self, project_id: str, issue_id: str) -> dict[str, str]:
        return {"name": f"Ticket {issue_id}", "description_stripped": "do the thing"}

    async def set_state(
        self, project_id: str, issue_id: str, state_id: str
    ) -> dict[str, str]:
        if self._error is not None:
            raise self._error
        self.moves.append((project_id, issue_id, state_id))
        return {}


class FakeGitHub:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.created: list[dict[str, str]] = []

    async def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequest:
        self.created.append(
            {"repo": repo, "head": head, "base": base, "title": title, "body": body}
        )
        if self._error is not None:
            raise self._error
        return PullRequest(number=7, html_url=f"https://github.com/{repo}/pull/7")


async def _scheduler(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
    dispatcher: FakeDispatcher,
    volumes: FakeVolumes,
    plane: FakePlane | None = None,
    github: FakeGitHub | None = None,
) -> tuple[Scheduler, TicketStore]:
    await mappings.set_project("proj-1", enabled=True)
    await mappings.set_repo(
        "proj-1", "backend", github_repo="octo/backend", base_branch="main"
    )
    tickets = TicketStore(sessionmaker)
    scheduler = Scheduler(
        sessionmaker=sessionmaker,
        store=store,
        mappings=mappings,
        tickets=tickets,
        dispatcher=dispatcher,  # type: ignore[arg-type]
        volumes=volumes,  # type: ignore[arg-type]
        plane_factory=lambda _cfg: plane or FakePlane(),  # type: ignore[arg-type,return-value]
        github_factory=lambda _cfg: github or FakeGitHub(),  # type: ignore[arg-type,return-value]
    )
    return scheduler, tickets


async def _enqueue(
    sessionmaker: async_sessionmaker[AsyncSession],
    issue_id: str,
    *,
    trigger: str = "engineer",
    project: str = "proj-1",
) -> None:
    await enqueue_job(
        sessionmaker,
        source="plane",
        event_type="issue.updated",
        payload={"project_id": project, "issue_id": issue_id, "trigger": trigger},
        dedupe_key=f"{project}:{issue_id}",
    )


async def _job_status(
    sessionmaker: async_sessionmaker[AsyncSession], issue_id: str
) -> str:
    async with sessionmaker() as session:
        job = (
            await session.execute(
                select(Job).where(Job.dedupe_key == f"proj-1:{issue_id}")
            )
        ).scalar_one()
        return job.status


async def test_tick_dispatches_engineer(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(session_id="s-42"), FakeVolumes()
    github = FakeGitHub()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, github=github
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    assert await scheduler.tick() is True
    assert len(dispatcher.runs) == 1
    run = dispatcher.runs[0]
    assert run.ticket_id == "ISSUE-1"
    assert run.role.value == "engineer"
    assert (
        "ISSUE-1" in run.prompt and "do the thing" in run.prompt
    )  # real engineer prompt
    assert volumes.prepared[0]["github_repo"] == "octo/backend"
    # The branch is pushed and a PR opened before the ticket moves to in_review.
    assert volumes.pushed == [{"ticket_id": "ISSUE-1", "github_repo": "octo/backend"}]
    assert github.created[0] == {
        "repo": "octo/backend",
        "head": "ticket/ISSUE-1",
        "base": "main",
        "title": "Ticket ISSUE-1",
        "body": github.created[0]["body"],
    }
    assert await _job_status(sessionmaker, "ISSUE-1") == "done"
    assert await tickets.in_flight_ids() == {"ISSUE-1"}  # left in in_review
    async with sessionmaker() as session:
        ticket = await session.get(Ticket, "ISSUE-1")
        assert ticket is not None
        assert ticket.pr_number == 7
        assert ticket.pr_url == "https://github.com/octo/backend/pull/7"


async def test_no_jobs_returns_false(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    assert await scheduler.tick() is False


async def test_planner_trigger_left_queued(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    await _enqueue(sessionmaker, "EPIC-1", trigger="planner")

    assert await scheduler.tick() is False
    assert dispatcher.runs == []
    assert await _job_status(sessionmaker, "EPIC-1") == "queued"


async def test_in_flight_ticket_outranks_older_fresh(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes
    )
    # FRESH is enqueued first (older); INFLIGHT is newer but already past the gate.
    await _enqueue(sessionmaker, "FRESH")
    await _enqueue(sessionmaker, "INFLIGHT")
    await tickets.get_or_create("INFLIGHT", "proj-1")
    await tickets.set_status("INFLIGHT", "changes_requested")

    await scheduler.tick()
    assert dispatcher.runs[0].ticket_id == "INFLIGHT"


async def test_oldest_first_when_none_in_flight(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    await _enqueue(sessionmaker, "OLDER")
    await _enqueue(sessionmaker, "NEWER")

    await scheduler.tick()
    assert dispatcher.runs[0].ticket_id == "OLDER"


async def test_dispatch_failure_marks_failed(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher = FakeDispatcher(error=RuntimeError("boom"))
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, FakeVolumes()
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()
    assert await _job_status(sessionmaker, "ISSUE-1") == "failed"
    assert await tickets.in_flight_ids() == set()  # moved to "error", not in-flight


async def test_pr_failure_marks_failed_and_not_in_review(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from conductor.github import GitHubError

    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    github = FakeGitHub(error=GitHubError("PR creation failed"))
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, github=github
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()
    assert await _job_status(sessionmaker, "ISSUE-1") == "failed"
    assert await tickets.in_flight_ids() == set()  # error, never reached in_review


async def test_no_repo_marks_failed(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    await mappings.delete_repo("proj-1", "backend")
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()
    assert dispatcher.runs == []
    assert await _job_status(sessionmaker, "ISSUE-1") == "failed"


async def test_board_mirroring_moves_the_card(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_state("proj-1", WorkflowState.IN_PROGRESS, "state-inprog")
    await mappings.set_state("proj-1", WorkflowState.IN_REVIEW, "state-inreview")
    plane = FakePlane()
    scheduler, _ = await _scheduler(
        store, mappings, sessionmaker, FakeDispatcher(), FakeVolumes(), plane
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()
    assert plane.moves == [
        ("proj-1", "ISSUE-1", "state-inprog"),
        ("proj-1", "ISSUE-1", "state-inreview"),
    ]


async def test_unmapped_state_skips_board_move(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    plane = FakePlane()  # no state mappings set → nothing to move to
    scheduler, _ = await _scheduler(
        store, mappings, sessionmaker, FakeDispatcher(), FakeVolumes(), plane
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()
    assert plane.moves == []
    assert await _job_status(sessionmaker, "ISSUE-1") == "done"


async def test_plane_error_does_not_fail_dispatch(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_state("proj-1", WorkflowState.IN_PROGRESS, "state-inprog")
    plane = FakePlane(error=PlaneError("plane down"))
    scheduler, _ = await _scheduler(
        store, mappings, sessionmaker, FakeDispatcher(), FakeVolumes(), plane
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()
    assert (
        await _job_status(sessionmaker, "ISSUE-1") == "done"
    )  # board move is best-effort


async def test_recover_requeues_orphaned_running_job(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    scheduler, _ = await _scheduler(
        store, mappings, sessionmaker, FakeDispatcher(), FakeVolumes()
    )
    await _enqueue(sessionmaker, "ISSUE-1")
    async with sessionmaker() as session:
        job = (await session.execute(select(Job))).scalar_one()
        job.status = "running"
        await session.commit()

    await scheduler.recover()
    assert await _job_status(sessionmaker, "ISSUE-1") == "queued"
