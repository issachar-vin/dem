from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from typing import Any

from conductor.agents.contracts import (
    ClaudeEnvelope,
    MalformedAgentOutput,
    Plan,
    PlannedTicket,
    Verdict,
)
from conductor.agents.dispatcher import AgentRun
from conductor.github import PullRequest
from conductor.jobs import enqueue_job
from conductor.mappings import MappingStore
from conductor.models import Job, Ticket, WorkflowState
from conductor.plane import PlaneError
from conductor.scheduler import Scheduler
from conductor.store import ConfigStore
from conductor.tickets import TicketStore


def _pass() -> Verdict:
    return Verdict.model_validate({"pass": True})


def _fail(comment: str = "broken") -> Verdict:
    return Verdict.model_validate(
        {"pass": False, "findings": [{"severity": "high", "comment": comment}]}
    )


class FakeDispatcher:
    def __init__(
        self,
        *,
        session_id: str = "sess-1",
        error: Exception | None = None,
        verdicts: list[Verdict] | None = None,
        parse_error: Exception | None = None,
        plan: Plan | None = None,
        engineer_result: str = "ok",
    ) -> None:
        self._session_id = session_id
        self._error = error
        # Verdicts returned by run_parsed in order (reviewer, qa, reviewer, qa, …); default: all pass.
        self._verdicts = list(verdicts) if verdicts is not None else None
        self._parse_error = parse_error
        self._plan = plan if plan is not None else Plan(tickets=[])
        self._engineer_result = engineer_result
        self.runs: list[AgentRun] = []
        self.parsed_runs: list[AgentRun] = []

    async def run(self, run: AgentRun) -> ClaudeEnvelope:
        self.runs.append(run)
        if self._error is not None:
            raise self._error
        return ClaudeEnvelope(session_id=self._session_id, result=self._engineer_result)

    async def run_parsed(self, run: AgentRun, parse: Any) -> tuple[ClaudeEnvelope, Any]:
        self.parsed_runs.append(run)
        if self._parse_error is not None:
            raise self._parse_error
        envelope = ClaudeEnvelope(session_id=self._session_id, result="ok")
        if run.role.value == "planner":
            return envelope, self._plan
        verdict = self._verdicts.pop(0) if self._verdicts else _pass()
        return envelope, verdict


class FakeVolumes:
    def __init__(
        self,
        *,
        diff_hashes: list[str] | None = None,
        commit_count: int = 1,
        session_exists: bool = False,
    ) -> None:
        self.prepared: list[dict[str, str]] = []
        self.pushed: list[dict[str, str]] = []
        self.planner_prepared: list[dict[str, Any]] = []
        self.destroyed: list[str] = []
        self._commit_count = commit_count
        self._session_exists = session_exists
        # Default hashes are unique per call so the stall detector never fires; a test that wants a
        # stall passes repeated values.
        self._diff_hashes = list(diff_hashes) if diff_hashes is not None else None
        self._diff_calls = 0

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

    async def prepare_planner(self, *, epic_id: str, repos: Any) -> None:
        self.planner_prepared.append({"epic_id": epic_id, "repos": list(repos)})

    async def has_session(self, *, ticket_id: str) -> bool:
        return self._session_exists

    async def destroy(self, *, ticket_id: str) -> None:
        self.destroyed.append(ticket_id)

    async def commit_count(self, *, ticket_id: str, base_branch: str) -> int:
        return self._commit_count

    async def diff_hash(self, *, ticket_id: str, base_branch: str) -> str:
        self._diff_calls += 1
        if self._diff_hashes is not None:
            return self._diff_hashes.pop(0)
        return f"hash-{self._diff_calls}"


class FakePlane:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        thread: list[dict[str, Any]] | None = None,
    ) -> None:
        self._error = error
        self._thread = thread or []
        self.moves: list[tuple[str, str, str]] = []
        self.comments: list[tuple[str, str, str]] = []
        self.created_issues: list[dict[str, Any]] = []

    async def list_comments(
        self, project_id: str, issue_id: str
    ) -> list[dict[str, Any]]:
        return self._thread

    async def get_issue(self, project_id: str, issue_id: str) -> dict[str, str]:
        return {"name": f"Ticket {issue_id}", "description_stripped": "do the thing"}

    async def create_issue(
        self, project_id: str, *, name: str, **fields: Any
    ) -> dict[str, str]:
        self.created_issues.append({"name": name, **fields})
        issue_id = f"issue-{len(self.created_issues)}"
        return {"id": issue_id, "name": name}

    async def post_comment(
        self, project_id: str, issue_id: str, comment_html: str
    ) -> dict[str, str]:
        self.comments.append((project_id, issue_id, comment_html))
        return {}

    async def set_state(
        self, project_id: str, issue_id: str, state_id: str
    ) -> dict[str, str]:
        if self._error is not None:
            raise self._error
        self.moves.append((project_id, issue_id, state_id))
        return {}


class FakeNotify:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, cfg: dict[str, str], message: str) -> None:
        self.messages.append(message)


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
    notify: FakeNotify | None = None,
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
        notify_fn=notify or FakeNotify(),
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


async def _enqueue_github(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    repo: str = "octo/backend",
    pr_number: int = 7,
    merged: bool = True,
    delivery_id: str | None = None,
) -> None:
    await enqueue_job(
        sessionmaker,
        source="github",
        event_type="pull_request.closed",
        payload={
            "project_id": "proj-1",
            "repo": repo,
            "pr_number": pr_number,
            "merged": merged,
        },
        delivery_id=delivery_id or f"gh-{repo}-{pr_number}-{merged}",
    )


async def _github_job_status(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        job = (
            await session.execute(select(Job).where(Job.source == "github"))
        ).scalar_one()
        return job.status


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


async def _event_messages(sessionmaker: async_sessionmaker[AsyncSession]) -> list[str]:
    from conductor.models import JobEvent

    async with sessionmaker() as session:
        rows = (
            await session.execute(select(JobEvent.message).order_by(JobEvent.id.asc()))
        ).scalars()
        return list(rows)


async def test_tick_dispatches_engineer(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(session_id="s-42"), FakeVolumes()
    github, plane = FakeGitHub(), FakePlane()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, plane=plane, github=github
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
    # The branch is pushed and a PR opened before review runs.
    assert volumes.pushed == [{"ticket_id": "ISSUE-1", "github_repo": "octo/backend"}]
    assert github.created[0] == {
        "repo": "octo/backend",
        "head": "ticket/ISSUE-1",
        "base": "main",
        "title": "Ticket ISSUE-1",
        "body": github.created[0]["body"],
    }
    # Reviewer + QA both ran and passed → ready_for_approval, job done.
    assert [r.role.value for r in dispatcher.parsed_runs] == ["reviewer", "qa"]
    assert await _job_status(sessionmaker, "ISSUE-1") == "done"
    async with sessionmaker() as session:
        ticket = await session.get(Ticket, "ISSUE-1")
        assert ticket is not None
        assert ticket.agent_status == "ready_for_approval"
        assert ticket.pr_number == 7
        assert ticket.pr_url == "https://github.com/octo/backend/pull/7"
    # The PR link is posted to the ticket as a comment (before review).
    assert any("pull/7" in c[2] for c in plane.comments)
    # The console timeline captured the conductor's pipeline steps.
    events = await _event_messages(sessionmaker)
    assert any("Preparing repository" in e for e in events)
    assert any("Opened PR #7" in e for e in events)
    assert any("Review passed" in e for e in events)


async def test_no_changes_parks_ticket(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Engineer left zero commits → no PR to open; park the ticket instead of 422-ing.
    dispatcher, volumes = FakeDispatcher(), FakeVolumes(commit_count=0)
    github, plane = FakeGitHub(), FakePlane()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, plane=plane, github=github
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    assert await scheduler.tick() is True
    assert github.created == []  # no PR attempted
    assert volumes.pushed == []
    assert len(plane.comments) == 1  # reason posted to the ticket
    assert (
        await _job_status(sessionmaker, "ISSUE-1") == "done"
    )  # a valid outcome, not a failure
    ticket = await tickets.get("ISSUE-1")
    assert ticket is not None and ticket.agent_status == "no_changes"


async def test_needs_input_parks_ticket(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_state("proj-1", WorkflowState.BLOCKED, "state-blocked")
    dispatcher = FakeDispatcher(
        engineer_result="NEEDS_INPUT: which auth provider should I use?"
    )
    volumes, github, plane = FakeVolumes(), FakeGitHub(), FakePlane()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, plane=plane, github=github
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    assert await scheduler.tick() is True
    assert github.created == []  # parked before PR creation
    assert (
        "which auth provider" in plane.comments[0][2]
    )  # the question reached the ticket
    ticket = await tickets.get("ISSUE-1")
    assert ticket is not None and ticket.agent_status == "awaiting_human"
    # A parked ticket is surfaced on the Plane board's blocked column.
    assert ("proj-1", "ISSUE-1", "state-blocked") in plane.moves
    assert await _job_status(sessionmaker, "ISSUE-1") == "done"


async def test_parked_ticket_resumes_with_memory(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # A previously parked ticket with a saved session, moved back to ready_for_dev.
    tickets = TicketStore(sessionmaker)
    await tickets.get_or_create("ISSUE-1", "proj-1")
    await tickets.set_status("ISSUE-1", "awaiting_human")
    await tickets.set_engineer_session("ISSUE-1", "prev-sess")

    dispatcher, volumes = FakeDispatcher(), FakeVolumes(session_exists=True)
    plane = FakePlane(thread=[{"comment_stripped": "Use OAuth, not API keys."}])
    scheduler, _ = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, plane=plane
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()

    # Resumed with memory: no fresh clone, and the engineer continued its saved session with the
    # human's answer folded into the prompt.
    assert volumes.prepared == []
    engineer = next(r for r in dispatcher.runs if r.role.value == "engineer")
    assert engineer.resume_session_id == "prev-sess"
    assert "Use OAuth" in engineer.prompt


async def test_parked_ticket_rebuilds_when_volumes_gone(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tickets = TicketStore(sessionmaker)
    await tickets.get_or_create("ISSUE-1", "proj-1")
    await tickets.set_status("ISSUE-1", "awaiting_human")
    await tickets.set_engineer_session("ISSUE-1", "prev-sess")

    dispatcher = FakeDispatcher()
    volumes = FakeVolumes(session_exists=False)  # volumes pruned since the park
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()

    # No session to resume → fresh build: re-cloned, dispatched without a resume session.
    assert volumes.prepared != []
    engineer = next(r for r in dispatcher.runs if r.role.value == "engineer")
    assert engineer.resume_session_id is None


async def test_review_fail_then_pass_resumes_engineer(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Round 1: reviewer fails, qa passes → findings fed back, engineer resumes; round 2: both pass.
    dispatcher = FakeDispatcher(
        verdicts=[_fail("null deref"), _pass(), _pass(), _pass()]
    )
    volumes, plane, notify = FakeVolumes(), FakePlane(), FakeNotify()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, plane=plane, notify=notify
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()

    # Two engineer runs: the initial build, then a resume carrying the findings.
    engineer_runs = [r for r in dispatcher.runs if r.role.value == "engineer"]
    assert len(engineer_runs) == 2
    assert engineer_runs[1].resume_session_id == "sess-1"
    assert "null deref" in engineer_runs[1].prompt
    # The PR-opened comment, then the round-1 findings comment.
    assert any("Pull request opened" in c[2] for c in plane.comments)
    assert any("null deref" in c[2] for c in plane.comments)
    assert any("ready for approval" in m for m in notify.messages)
    async with sessionmaker() as session:
        ticket = await session.get(Ticket, "ISSUE-1")
        assert ticket is not None
        assert ticket.agent_status == "ready_for_approval"
        assert ticket.loop_round == 1
    assert await _job_status(sessionmaker, "ISSUE-1") == "done"


async def test_stall_detection_marks_stalled(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher = FakeDispatcher(verdicts=[_fail("still broken"), _pass()])
    # Identical diff hash before and after the resume → the engineer changed nothing → stalled.
    volumes = FakeVolumes(diff_hashes=["h", "h"])
    notify = FakeNotify()
    scheduler, _ = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, notify=notify
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()

    assert any("stalled" in m for m in notify.messages)
    assert (
        await _job_status(sessionmaker, "ISSUE-1") == "done"
    )  # loop terminated cleanly
    async with sessionmaker() as session:
        ticket = await session.get(Ticket, "ISSUE-1")
        assert ticket is not None
        assert ticket.agent_status == "stalled"


async def test_unscorable_verdict_parks_with_pr_not_failed(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_state("proj-1", WorkflowState.BLOCKED, "state-blocked")
    dispatcher = FakeDispatcher(parse_error=MalformedAgentOutput("still not JSON"))
    github, plane = FakeGitHub(), FakePlane()
    scheduler, _ = await _scheduler(
        store,
        mappings,
        sessionmaker,
        dispatcher,
        FakeVolumes(),
        plane=plane,
        github=github,
    )
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()

    # A PR was opened before review, so an unscorable verdict parks for a human — it does not fail
    # the job and discard the work.
    assert github.created  # PR was opened
    assert await _job_status(sessionmaker, "ISSUE-1") == "done"
    async with sessionmaker() as session:
        ticket = await session.get(Ticket, "ISSUE-1")
        assert ticket is not None and ticket.agent_status == "review_unscored"
    # PR link is posted to the ticket and the board shows blocked.
    assert any("pull/7" in c[2] for c in plane.comments)
    assert ("proj-1", "ISSUE-1", "state-blocked") in plane.moves


async def test_no_jobs_returns_false(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    assert await scheduler.tick() is False


async def test_planner_creates_tickets_with_repo_and_blocking(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    plan = Plan(
        tickets=[
            PlannedTicket(key="T1", title="Backend API", target_repo="backend"),
            PlannedTicket(
                key="T2", title="UI on top", target_repo="backend", blocked_by=["T1"]
            ),
        ]
    )
    dispatcher, volumes, plane = FakeDispatcher(plan=plan), FakeVolumes(), FakePlane()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes, plane=plane
    )
    await mappings.set_state("proj-1", WorkflowState.READY_FOR_DEV, "rfd-state")
    await _enqueue(sessionmaker, "EPIC-1", trigger="planner")

    assert await scheduler.tick() is True
    # The planner ran once (parsed) and the repo set was cloned read-only.
    assert dispatcher.parsed_runs[0].role.value == "planner"
    assert volumes.planner_prepared[0]["epic_id"] == "EPIC-1"
    # Two Plane issues created in ready_for_dev.
    assert len(plane.created_issues) == 2
    assert plane.created_issues[0]["state"] == "rfd-state"
    # Local tickets carry target repo + resolved blocking graph (T2 blocked by T1's real id).
    t1 = await tickets.get("issue-1")
    t2 = await tickets.get("issue-2")
    assert t1 is not None and t1.target_repo == "backend" and t1.blocked_by == []
    assert t2 is not None and t2.blocked_by == ["issue-1"]
    assert await _job_status(sessionmaker, "EPIC-1") == "done"


async def test_planner_no_repo_marks_failed(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(plan=Plan(tickets=[])), FakeVolumes()
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    await mappings.delete_repo("proj-1", "backend")
    await _enqueue(sessionmaker, "EPIC-1", trigger="planner")

    await scheduler.tick()
    assert dispatcher.parsed_runs == []  # never dispatched — no repo to clone
    assert await _job_status(sessionmaker, "EPIC-1") == "failed"


async def test_engineer_routes_to_planner_assigned_repo(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes
    )
    await mappings.set_repo("proj-1", "ui", github_repo="octo/ui", base_branch="dev")
    # Planner assigned this ticket the "ui" repo, not the default first ("backend").
    await tickets.create_planned("ISSUE-1", "proj-1", target_repo="ui", blocked_by=[])
    await _enqueue(sessionmaker, "ISSUE-1")

    await scheduler.tick()
    assert volumes.prepared[0]["github_repo"] == "octo/ui"
    assert volumes.prepared[0]["base_branch"] == "dev"


async def test_blocked_ticket_is_skipped_until_blocker_done(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes
    )
    await tickets.create_planned(
        "DEP", "proj-1", target_repo="backend", blocked_by=["BLOCKER"]
    )
    await _enqueue(sessionmaker, "DEP")

    # BLOCKER not done → DEP is blocked, nothing dispatches.
    assert await scheduler.tick() is False
    assert dispatcher.runs == []

    await tickets.create_planned(
        "BLOCKER", "proj-1", target_repo="backend", blocked_by=[]
    )
    await tickets.set_status("BLOCKER", "done")
    assert await scheduler.tick() is True  # blocker done → DEP dispatches
    assert dispatcher.runs[0].ticket_id == "DEP"


async def test_merged_pr_cleans_up_ticket(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes
    )
    await tickets.get_or_create("ISSUE-1", "proj-1")
    await tickets.set_pr("ISSUE-1", 7, "https://github.com/octo/backend/pull/7")
    await tickets.set_status("ISSUE-1", "in_review")
    await _enqueue_github(sessionmaker, repo="octo/backend", pr_number=7, merged=True)

    assert await scheduler.tick() is True
    assert volumes.destroyed == ["ISSUE-1"]
    assert await _github_job_status(sessionmaker) == "done"
    ticket = await tickets.get("ISSUE-1")
    assert ticket is not None and ticket.agent_status == "done"


async def test_unmerged_pr_job_is_drained(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes
    )
    await tickets.get_or_create("ISSUE-1", "proj-1")
    await tickets.set_pr("ISSUE-1", 7, "https://github.com/octo/backend/pull/7")
    await _enqueue_github(sessionmaker, pr_number=7, merged=False)

    assert await scheduler.tick() is True
    assert volumes.destroyed == []  # not merged → no cleanup
    assert await _github_job_status(sessionmaker) == "done"  # drained, not left queued


async def test_merged_pr_without_tracked_ticket_is_drained(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, _ = await _scheduler(store, mappings, sessionmaker, dispatcher, volumes)
    await _enqueue_github(sessionmaker, pr_number=99, merged=True)

    assert await scheduler.tick() is True
    assert volumes.destroyed == []
    assert await _github_job_status(sessionmaker) == "done"


async def test_merged_pr_releases_blocked_ticket(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dispatcher, volumes = FakeDispatcher(), FakeVolumes()
    scheduler, tickets = await _scheduler(
        store, mappings, sessionmaker, dispatcher, volumes
    )
    await tickets.create_planned(
        "DEP", "proj-1", target_repo="backend", blocked_by=["BLOCKER"]
    )
    await tickets.create_planned(
        "BLOCKER", "proj-1", target_repo="backend", blocked_by=[]
    )
    await tickets.set_pr("BLOCKER", 7, "https://github.com/octo/backend/pull/7")
    await _enqueue(sessionmaker, "DEP")

    assert await scheduler.tick() is False  # DEP blocked by BLOCKER (not done)

    await _enqueue_github(sessionmaker, pr_number=7, merged=True)
    assert await scheduler.tick() is True  # merged PR → BLOCKER done
    assert volumes.destroyed == ["BLOCKER"]

    assert await scheduler.tick() is True  # DEP now unblocked → dispatched
    assert dispatcher.runs[0].ticket_id == "DEP"


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
