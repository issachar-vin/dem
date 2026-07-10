"""The scheduler: the piece that finally *consumes* intake Jobs. On an interval it selects the
next engineer ticket to build — in-flight work first, then the oldest `ready_for_dev` — gated by
Plane blocking relationships, and dispatches one agent container per role (MAX_CONCURRENT_AGENTS).

The engineer trigger runs the full build+review pipeline for one ticket, synchronously within one
job: build the ticket in a container, push its branch, open a PR, then loop reviewer + QA — feeding
their findings back to the engineer via `--resume` until both pass (→ ready_for_approval) or the
engineer's diff stops changing (→ stalled). The Plane board is mirrored across the transitions. The
planner and GitHub triggers are later Phase-5 parts; those jobs are left queued for now."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import github, notify, plane, prompts
from conductor.agents import contracts
from conductor.agents.contracts import Finding, Verdict
from conductor.agents.dispatcher import AgentRun, Dispatcher
from conductor.agents.roles import MODEL_SETTING, AgentRole
from conductor.agents.volumes import VolumeManager
from conductor.github import GitHubClient
from conductor.jobs import claim_job, complete_job, requeue_running
from conductor.mappings import MappingStore
from conductor.models import Job, JobStatus, WorkflowState
from conductor.plane import PlaneClient
from conductor.store import ConfigStore
from conductor.tickets import TicketStore

logger = logging.getLogger("conductor")

_INTERVAL_SECONDS = 5.0
_ENGINEER_TRIGGER = "engineer"
_CHECKER_ROLES = (AgentRole.REVIEWER, AgentRole.QA)
_DEFAULT_MODEL = {AgentRole.ENGINEER: "claude-sonnet-4-6"}


@dataclass(frozen=True)
class _Pipeline:
    """Everything the engineer→review loop for one ticket needs, resolved once at the top of a
    dispatch so the loop methods take one argument instead of eight."""

    cfg: dict[str, str]
    plane: PlaneClient
    github: GitHubClient
    project_id: str
    issue_id: str
    repo: str
    base_branch: str
    issue: dict[str, Any]
    pr_url: str = ""


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
        github_factory: Callable[[dict[str, str]], GitHubClient] = github.client_from_resolved,
        notify_fn: Callable[[dict[str, str], str], Awaitable[None]] = notify.notify,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._store = store
        self._mappings = mappings
        self._tickets = tickets
        self._dispatcher = dispatcher
        self._volumes = volumes
        self._plane_factory = plane_factory
        self._github_factory = github_factory
        self._notify = notify_fn

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
            issue = await plane_client.get_issue(project_id, issue_id)
            ctx = _Pipeline(
                cfg=cfg,
                plane=plane_client,
                github=self._github_factory(cfg),
                project_id=project_id,
                issue_id=issue_id,
                repo=repo,
                base_branch=base_branch,
                issue=issue,
            )
            await self._tickets.get_or_create(issue_id, project_id)
            ctx, session_id = await self._build(ctx)
            await self._review_loop(ctx, session_id)
            await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
        except Exception as exc:
            await complete_job(self._sessionmaker, job.id, status=JobStatus.FAILED, error=str(exc))
            if issue_id:
                await self._tickets.set_status(issue_id, "error")
            logger.exception("Ticket pipeline failed for %s", issue_id)

    async def _build(self, ctx: _Pipeline) -> tuple[_Pipeline, str]:
        """Engineer stage: build the ticket, push its branch, open the PR. Returns the enriched
        context (with the PR url) and the engineer's session id for the review-loop resume."""
        await self._set_state(ctx, "in_progress", WorkflowState.IN_PROGRESS)
        await self._volumes.prepare(
            ticket_id=ctx.issue_id, github_repo=ctx.repo, base_branch=ctx.base_branch
        )
        envelope = await self._dispatcher.run(
            AgentRun(
                role=AgentRole.ENGINEER,
                ticket_id=ctx.issue_id,
                prompt=_engineer_prompt(ctx.issue_id, ctx.issue),
                model=self._model(ctx, AgentRole.ENGINEER),
            )
        )
        await self._tickets.set_engineer_session(ctx.issue_id, envelope.session_id)
        await self._volumes.push(ticket_id=ctx.issue_id, github_repo=ctx.repo)
        pr = await ctx.github.create_pull_request(
            ctx.repo,
            head=f"ticket/{ctx.issue_id}",
            base=ctx.base_branch,
            title=_issue_title(ctx.issue),
            body=_pr_body(ctx.issue_id, ctx.issue),
        )
        await self._tickets.set_pr(ctx.issue_id, pr.number, pr.html_url)
        await self._set_state(ctx, "in_review", WorkflowState.IN_REVIEW)
        logger.info("Engineer built ticket %s → PR %s", ctx.issue_id, pr.number)
        return replace(ctx, pr_url=pr.html_url), envelope.session_id

    async def _review_loop(self, ctx: _Pipeline, session_id: str) -> None:
        """Run reviewer + QA; on any fail, feed findings back to the engineer and re-review until
        both pass (→ ready_for_approval) or the engineer's diff stops changing (→ stalled)."""
        last_hash = await self._volumes.diff_hash(
            ticket_id=ctx.issue_id, base_branch=ctx.base_branch
        )
        await self._tickets.set_diff_hash(ctx.issue_id, last_hash)
        while True:
            verdicts = await self._run_checkers(ctx)
            if all(verdict.passed for _, verdict in verdicts):
                await self._set_state(ctx, "ready_for_approval", WorkflowState.READY_FOR_APPROVAL)
                await self._notify(
                    ctx.cfg, f"Ticket {ctx.issue_id} ready for approval: {ctx.pr_url}"
                )
                logger.info("Ticket %s passed review → ready_for_approval", ctx.issue_id)
                return
            await self._post_findings(ctx, verdicts)
            round_no = await self._tickets.bump_loop_round(ctx.issue_id)
            await self._set_state(ctx, "changes_requested", WorkflowState.CHANGES_REQUESTED)
            await self._resume_engineer(ctx, session_id, verdicts, round_no)
            await self._volumes.push(ticket_id=ctx.issue_id, github_repo=ctx.repo)
            new_hash = await self._volumes.diff_hash(
                ticket_id=ctx.issue_id, base_branch=ctx.base_branch
            )
            if new_hash == last_hash:  # engineer resume produced an identical diff → stalled
                await self._tickets.set_status(ctx.issue_id, "stalled")
                await self._notify(
                    ctx.cfg, f"Ticket {ctx.issue_id} stalled at round {round_no}: {ctx.pr_url}"
                )
                logger.warning("Ticket %s stalled at round %d", ctx.issue_id, round_no)
                return
            last_hash = new_hash
            await self._tickets.set_diff_hash(ctx.issue_id, new_hash)

    async def _run_checkers(self, ctx: _Pipeline) -> list[tuple[AgentRole, Verdict]]:
        """Reviewer then QA (sequential — the MAX_CONCURRENT_AGENTS=1 default). Each verdict is
        parsed with the dispatcher's re-prompt-once policy; a still-malformed verdict raises and
        parks the ticket."""
        verdicts: list[tuple[AgentRole, Verdict]] = []
        for role in _CHECKER_ROLES:
            _, verdict = await self._dispatcher.run_parsed(
                AgentRun(
                    role=role,
                    ticket_id=ctx.issue_id,
                    prompt=_checker_prompt(role, ctx),
                    model=self._model(ctx, role),
                ),
                contracts.parse_verdict,
            )
            verdicts.append((role, verdict))
        return verdicts

    async def _resume_engineer(
        self,
        ctx: _Pipeline,
        session_id: str,
        verdicts: list[tuple[AgentRole, Verdict]],
        round_no: int,
    ) -> None:
        await self._dispatcher.run(
            AgentRun(
                role=AgentRole.ENGINEER,
                ticket_id=ctx.issue_id,
                prompt=prompts.render(
                    "engineer_followup",
                    ticket_id=ctx.issue_id,
                    findings=_format_findings(verdicts),
                ),
                model=self._model(ctx, AgentRole.ENGINEER),
                resume_session_id=session_id,
                loop_round=round_no,
            )
        )

    async def _post_findings(
        self, ctx: _Pipeline, verdicts: list[tuple[AgentRole, Verdict]]
    ) -> None:
        try:
            await ctx.plane.post_comment(
                ctx.project_id, ctx.issue_id, _findings_comment_html(verdicts)
            )
        except plane.PlaneError as exc:  # the resume still carries the findings in its prompt
            logger.warning("Could not post findings to issue %s: %s", ctx.issue_id, exc.detail)

    def _model(self, ctx: _Pipeline, role: AgentRole) -> str:
        return ctx.cfg.get(MODEL_SETTING[role], _DEFAULT_MODEL.get(role, "claude-haiku-4-5"))

    async def _set_state(
        self, ctx: _Pipeline, local_status: str, workflow_state: WorkflowState
    ) -> None:
        """Advance the ticket's local status and mirror it onto the Plane board. Best-effort: an
        unmapped state or a Plane error is logged, and the dispatch still continues."""
        await self._tickets.set_status(ctx.issue_id, local_status)
        state_id = await self._mappings.get_state_id(ctx.project_id, workflow_state)
        if state_id is None:
            logger.info(
                "No Plane state mapped for %s (project %s); board not moved",
                workflow_state,
                ctx.project_id,
            )
            return
        try:
            await ctx.plane.set_state(ctx.project_id, ctx.issue_id, state_id)
        except plane.PlaneError as exc:
            logger.warning(
                "Could not move issue %s to %s in Plane: %s",
                ctx.issue_id,
                workflow_state,
                exc.detail,
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


def _issue_title(issue: dict[str, object]) -> str:
    return str(issue.get("name") or "Untitled ticket")


def _issue_body(issue: dict[str, object]) -> str:
    # Plane CE has no dedicated acceptance-criteria field; the description carries both. Prefer the
    # plain-text rendering, falling back to the HTML if that is all Plane returned.
    return str(issue.get("description_stripped") or issue.get("description_html") or "")


def _engineer_prompt(issue_id: str, issue: dict[str, object]) -> str:
    return prompts.render(
        "engineer", ticket_id=issue_id, title=_issue_title(issue), body=_issue_body(issue)
    )


def _pr_body(issue_id: str, issue: dict[str, object]) -> str:
    body = _issue_body(issue)
    return f"Automated implementation of ticket `{issue_id}`.\n\n{body}".rstrip()


def _checker_prompt(role: AgentRole, ctx: _Pipeline) -> str:
    return prompts.render(
        role.value,
        ticket_id=ctx.issue_id,
        base_branch=ctx.base_branch,
        title=_issue_title(ctx.issue),
        body=_issue_body(ctx.issue),
    )


def _format_findings(verdicts: list[tuple[AgentRole, Verdict]]) -> str:
    """The findings from the failing checkers, as a markdown list fed to the engineer's resume."""
    lines: list[str] = []
    for role, verdict in verdicts:
        if verdict.passed:
            continue
        for finding in verdict.findings:
            loc = _finding_location(finding)
            lines.append(f"- **[{role.value}/{finding.severity}]** {loc}{finding.comment}")
    return "\n".join(lines)


def _findings_comment_html(verdicts: list[tuple[AgentRole, Verdict]]) -> str:
    parts: list[str] = []
    for role, verdict in verdicts:
        if verdict.passed:
            continue
        items = "".join(
            f"<li><b>{finding.severity}</b>: {_finding_location(finding)}{finding.comment}</li>"
            for finding in verdict.findings
        )
        parts.append(f"<p><b>{role.value} findings</b></p><ul>{items}</ul>")
    return "".join(parts)


def _finding_location(finding: Finding) -> str:
    if not finding.file:
        return ""
    where = finding.file if finding.line is None else f"{finding.file}:{finding.line}"
    return f"`{where}` — "


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
