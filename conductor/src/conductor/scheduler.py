"""The scheduler: the piece that finally *consumes* intake Jobs. On an interval it selects the
next engineer ticket to build — in-flight work first, then the oldest `ready_for_dev` — gated by
Plane blocking relationships, and dispatches one agent container per role (MAX_CONCURRENT_AGENTS).

The engineer trigger runs the full build+review pipeline for one ticket, synchronously within one
job: build the ticket in a container, push its branch, open a PR, then loop reviewer + QA — feeding
their findings back to the engineer via `--resume` until both pass (→ ready_for_approval) or the
engineer's diff stops changing (→ stalled). The planner trigger decomposes an epic: it clones the
project's repos read-only, dispatches the planner, then creates a Plane issue per planned ticket in
ready_for_dev (each carrying its target repo + blocking graph locally). GitHub PR jobs are handled
too: a merged PR marks its ticket `done` (releasing dependents from the blocking gate) and reclaims
its volumes; other PR deliveries are drained. The Plane board is mirrored across the transitions."""

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import github, job_events, notify, plane, router
from conductor.agents import contracts
from conductor.agents.contracts import Finding, Plan, Verdict
from conductor.agents.dispatcher import AgentRun, Dispatcher
from conductor.agents.roles import MODEL_SETTING, AgentRole
from conductor.agents.volumes import RepoClone, VolumeManager
from conductor.github import GitHubClient
from conductor.jobs import claim_job, complete_job, requeue_running
from conductor.mappings import MappingStore, RepoMappingView
from conductor.models import Job, JobStatus, Ticket, WorkflowState
from conductor.plane import PlaneClient
from conductor.prompts import PromptStore
from conductor.store import ConfigStore
from conductor.tickets import TicketStore

logger = logging.getLogger("conductor")

_INTERVAL_SECONDS = 5.0
_ENGINEER_TRIGGER = "engineer"
_PLANNER_TRIGGER = "planner"
_CHECKER_ROLES = (AgentRole.REVIEWER, AgentRole.QA)
_DEFAULT_MODEL = {AgentRole.ENGINEER: "claude-sonnet-4-6"}
# Parked local statuses whose ticket can be *resumed* (engineer paused mid-work, no PR yet) rather
# than rebuilt from a fresh clone when a human moves it back to ready_for_dev.
_RESUMABLE_PARKED = frozenset({"awaiting_human", "no_changes"})
_HTML_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class _Resume:
    """A parked ticket being un-blocked: continue its saved Claude session (memory + working tree
    intact) instead of a fresh build, feeding it the human's answer from the ticket thread."""

    session_id: str
    conversation: str


@dataclass(frozen=True)
class _Pipeline:
    """Everything the engineer→review loop for one ticket needs, resolved once at the top of a
    dispatch so the loop methods take one argument instead of eight."""

    cfg: dict[str, str]
    plane: PlaneClient
    github: GitHubClient
    job_id: int  # the driving job; scopes every agent run captured during this dispatch
    project_id: str
    issue_id: str
    targets: list[RepoClone]  # the repo(s) this ticket builds in, each cloned at /work/<key>
    issue: dict[str, Any]
    built: list[RepoClone] = field(default_factory=list)  # targets that got commits (→ a PR each)
    pr_url: str = ""  # first PR's url, for the ready-for-approval notify line


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
        prompts_store: PromptStore,
        plane_factory: Callable[[dict[str, str]], PlaneClient] = plane.client_from_resolved,
        github_factory: Callable[[dict[str, str]], GitHubClient] = github.client_from_resolved,
        notify_fn: Callable[[dict[str, str], str], Awaitable[None]] = notify.notify,
        route_fn: Callable[..., Awaitable[list[str]]] = router.route_repos,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._store = store
        self._mappings = mappings
        self._tickets = tickets
        self._dispatcher = dispatcher
        self._volumes = volumes
        self._prompts = prompts_store
        self._plane_factory = plane_factory
        self._github_factory = github_factory
        self._notify = notify_fn
        self._route_fn = route_fn

    async def tick(self) -> bool:
        """Select and run at most one job. Returns True if work was dispatched this tick."""
        job = await self._select_job()
        if job is None:
            return False
        if not await claim_job(self._sessionmaker, job.id):
            return False  # another worker won the race
        if job.source == "github":
            await self._run_cleanup(job)
        elif job.payload.get("trigger") == _PLANNER_TRIGGER:
            await self._run_planner(job)
        else:
            await self._run_engineer(job)
        return True

    async def _select_job(self) -> Job | None:
        async with self._sessionmaker() as session:
            jobs = (
                (
                    await session.execute(
                        select(Job)
                        .where(Job.status == JobStatus.QUEUED)
                        .order_by(Job.created_at.asc(), Job.id.asc())
                    )
                )
                .scalars()
                .all()
            )
        in_flight = await self._tickets.in_flight_ids()
        candidates: list[Job] = []
        for job in jobs:
            if job.source == "github":
                candidates.append(job)  # merged-PR cleanup / drain — always eligible
                continue
            if job.payload.get("trigger") not in (_ENGINEER_TRIGGER, _PLANNER_TRIGGER):
                continue
            if await self._is_blocked(job):
                continue
            candidates.append(job)
        # In-flight tickets outrank fresh ones; oldest-created breaks ties (already sorted asc).
        candidates.sort(key=lambda j: 0 if str(j.payload.get("issue_id")) in in_flight else 1)
        return candidates[0] if candidates else None

    async def _is_blocked(self, job: Job) -> bool:
        """A ticket is blocked until every issue in its `blocked_by` graph is `done` (merged — set
        by the Part-4 cleanup handler). The planner records the graph locally; a blocker not yet
        created, or not yet done, keeps the ticket queued. Epics/human tickets carry no graph."""
        issue_id = str(job.payload.get("issue_id", ""))
        ticket = await self._tickets.get(issue_id)
        if ticket is None or not ticket.blocked_by:
            return False
        statuses = await self._tickets.statuses_for(ticket.blocked_by)
        return any(statuses.get(blocker) != "done" for blocker in ticket.blocked_by)

    async def _run_cleanup(self, job: Job) -> None:
        """Handle a GitHub PR job. A merged PR is recorded against its ticket; only once **every**
        PR the ticket opened has merged is the ticket marked `done` (releasing dependents from the
        blocking gate) and its volumes reclaimed. Every other PR delivery (opened, review, comment,
        unmerged close) has no consumer and is drained so the queue can't grow unbounded."""
        try:
            pr_number = job.payload.get("pr_number")
            if not job.payload.get("merged") or not isinstance(pr_number, int):
                await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
                return
            pr_url = f"https://github.com/{job.payload.get('repo', '')}/pull/{pr_number}"
            ticket_id = await self._tickets.mark_pr_merged(pr_url)
            if ticket_id is None:
                await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
                logger.info("Merged PR %s has no tracked ticket; nothing to clean up", pr_url)
                return
            if not await self._tickets.all_prs_merged(ticket_id):
                await self._emit(
                    job.id,
                    f"Merged PR #{pr_number}; waiting on the ticket's other PR(s)",
                    ticket_id=ticket_id,
                    level="success",
                )
                await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
                return
            await self._tickets.set_status(ticket_id, "done")
            await self._volumes.destroy(ticket_id=ticket_id)
            await self._emit(
                job.id,
                f"All PRs merged (last: #{pr_number}) → done, volumes reclaimed",
                ticket_id=ticket_id,
                level="success",
            )
            await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
            logger.info("Ticket %s fully merged → done, volumes reclaimed", ticket_id)
        except Exception as exc:
            await complete_job(self._sessionmaker, job.id, status=JobStatus.FAILED, error=str(exc))
            await self._emit(job.id, f"Cleanup failed: {exc}", level="error")
            logger.exception("Cleanup failed for job %s", job.id)

    async def _run_engineer(self, job: Job) -> None:
        project_id = str(job.payload.get("project_id", ""))
        issue_id = str(job.payload.get("issue_id", ""))
        try:
            existing = await self._tickets.get(issue_id)
            cfg = await self._store.resolved()
            plane_client = self._plane_factory(cfg)
            issue = await plane_client.get_issue(project_id, issue_id)
            chosen = await self._route_repos(cfg, project_id, issue, existing)
            await self._emit(
                job.id,
                f"Determined repo(s) needed: {', '.join(r.key for r in chosen)}",
                ticket_id=issue_id,
            )
            targets = [RepoClone(r.key, r.github_repo, r.base_branch) for r in chosen]
            ctx = _Pipeline(
                cfg=cfg,
                plane=plane_client,
                github=self._github_factory(cfg),
                job_id=job.id,
                project_id=project_id,
                issue_id=issue_id,
                targets=targets,
                issue=issue,
            )
            await self._tickets.get_or_create(issue_id, project_id)
            resume = await self._resume_context(ctx, existing)
            built = await self._build(ctx, resume=resume)
            if built is not None:  # None → parked (needs input / no changes); no PR to review
                ctx, session_id = built
                try:
                    await self._review_loop(ctx, session_id)
                except contracts.MalformedAgentOutput as exc:
                    # A PR already exists, so an unscorable verdict is a park for a human, not a job
                    # failure that discards real work. Surface it on the board with the PR link.
                    await self._park(
                        ctx,
                        "review_unscored",
                        f"A reviewer/QA verdict could not be scored (unparseable agent output): "
                        f"{exc}\n\nThe PR is open for manual review: {ctx.pr_url}",
                    )
                    logger.warning(
                        "Review unscored for %s; parked with PR %s", issue_id, ctx.pr_url
                    )
            await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
        except Exception as exc:
            await complete_job(self._sessionmaker, job.id, status=JobStatus.FAILED, error=str(exc))
            await self._emit(job.id, f"Failed: {exc}", ticket_id=issue_id, level="error")
            if issue_id:
                # Don't clobber a ticket a concurrent console stop already parked as `stopped`.
                await self._tickets.set_status_unless(issue_id, "error", ("stopped",))
            logger.exception("Ticket pipeline failed for %s", issue_id)

    async def _run_planner(self, job: Job) -> None:
        project_id = str(job.payload.get("project_id", ""))
        epic_id = str(job.payload.get("issue_id", ""))
        try:
            cfg = await self._store.resolved()
            plane_client = self._plane_factory(cfg)
            repos = await self._mappings.list_repos(project_id)
            if not repos:
                raise RuntimeError(f"project {project_id} has no mapped repo")
            epic = await plane_client.get_issue(project_id, epic_id)
            await self._emit(
                job.id,
                f"Preparing planner workspace — cloning {len(repos)} repo(s)",
                ticket_id=epic_id,
            )
            await self._volumes.prepare_planner(
                epic_id=epic_id,
                repos=[RepoClone(r.key, r.github_repo, r.base_branch) for r in repos],
            )
            _, plan = await self._dispatcher.run_parsed(
                AgentRun(
                    role=AgentRole.PLANNER,
                    ticket_id=epic_id,
                    job_id=job.id,
                    prompt=await self._planner_prompt(epic, repos),
                    model=self._model(cfg, AgentRole.PLANNER),
                ),
                contracts.parse_plan,
            )
            await self._materialize_plan(plane_client, project_id, plan, repos)
            await self._emit(
                job.id,
                f"Decomposed epic into {len(plan.tickets)} ticket(s)",
                ticket_id=epic_id,
                level="success",
            )
            await complete_job(self._sessionmaker, job.id, status=JobStatus.DONE)
            logger.info("Planner decomposed epic %s into %d ticket(s)", epic_id, len(plan.tickets))
        except Exception as exc:
            await complete_job(self._sessionmaker, job.id, status=JobStatus.FAILED, error=str(exc))
            await self._emit(job.id, f"Planner failed: {exc}", ticket_id=epic_id, level="error")
            logger.exception("Planner failed for epic %s", epic_id)

    async def _materialize_plan(
        self, plane_client: PlaneClient, project_id: str, plan: Plan, repos: list[RepoMappingView]
    ) -> None:
        """Create a Plane issue per planned ticket (dropped into ready_for_dev so the webhook fires
        an engineer job) and pre-create the local Ticket carrying its target repo and — once every
        issue id is known — its resolved blocking graph."""
        repo_keys = {r.key for r in repos}
        ready_state = await self._mappings.get_state_id(project_id, WorkflowState.READY_FOR_DEV)
        if ready_state is None:
            logger.warning(
                "No ready_for_dev state mapped for project %s; planner tickets won't auto-dispatch",
                project_id,
            )
        created: list[tuple[Any, str, str | None]] = []
        for planned in plan.tickets:
            target = planned.target_repo if planned.target_repo in repo_keys else None
            if target is None:
                logger.warning(
                    "Planned ticket %s target_repo %r not in project repos; routing to first repo",
                    planned.key,
                    planned.target_repo,
                )
            fields: dict[str, Any] = {"description_html": _plan_issue_html(planned)}
            if ready_state:
                fields["state"] = ready_state
            issue = await plane_client.create_issue(project_id, name=planned.title, **fields)
            issue_id = str(issue.get("id", ""))
            if issue_id:
                created.append((planned, issue_id, target))
        key_to_id = {planned.key: issue_id for planned, issue_id, _ in created}
        for planned, issue_id, target in created:
            blocked_ids = [key_to_id[k] for k in planned.blocked_by if k in key_to_id]
            await self._tickets.create_planned(
                issue_id, project_id, target_repo=target, blocked_by=blocked_ids
            )

    async def _build(
        self, ctx: _Pipeline, *, resume: _Resume | None = None
    ) -> tuple[_Pipeline, str] | None:
        """Engineer stage: build the ticket across its target repos, then open one PR per repo that
        got commits. Returns the enriched context (its built repos + first PR url) and the
        engineer's session id for the review-loop resume — or `None` if the ticket was **parked**
        (the engineer asked a question, or made no changes in any repo).

        `resume` set → the ticket was parked and is being un-blocked: reuse its existing repo +
        Claude-session volumes (no wipe, no re-clone) and continue the same `claude` session with
        the human's answer, so the agent keeps its memory instead of rescanning the codebase."""
        await self._set_state(ctx, "in_progress", WorkflowState.IN_PROGRESS)
        keys = ", ".join(t.key for t in ctx.targets)
        if resume is None:
            await self._emit(
                ctx.job_id, f"Preparing repositories — cloning {keys}", ticket_id=ctx.issue_id
            )
            await self._volumes.prepare(ticket_id=ctx.issue_id, targets=ctx.targets)
            prompt = await self._engineer_prompt(ctx.issue_id, ctx.issue, ctx.targets)
        else:
            await self._emit(
                ctx.job_id,
                "Resuming from the saved session — reusing the existing checkout, no re-clone",
                ticket_id=ctx.issue_id,
            )
            prompt = await self._prompts.render(
                "engineer_resume", ticket_id=ctx.issue_id, conversation=resume.conversation
            )
        envelope = await self._dispatcher.run(
            AgentRun(
                role=AgentRole.ENGINEER,
                ticket_id=ctx.issue_id,
                job_id=ctx.job_id,
                prompt=prompt,
                model=self._model(ctx.cfg, AgentRole.ENGINEER),
                resume_session_id=resume.session_id if resume else None,
            )
        )
        await self._tickets.set_engineer_session(ctx.issue_id, envelope.session_id)

        question = _needs_input(envelope.result)
        if question is not None:
            await self._park(ctx, "awaiting_human", f"The engineer needs a decision:\n\n{question}")
            return None

        built = [
            t
            for t in ctx.targets
            if await self._volumes.commit_count(
                ticket_id=ctx.issue_id, key=t.key, base_branch=t.base_branch
            )
            > 0
        ]
        if not built:
            summary = envelope.result.strip() or "(no summary)"
            await self._park(ctx, "no_changes", f"The engineer made no changes.\n\n{summary}")
            return None

        prs = await self._open_prs(ctx, built)
        ctx = replace(ctx, built=built, pr_url=prs[0].html_url)
        await self._set_state(ctx, "in_review", WorkflowState.IN_REVIEW)
        logger.info("Engineer built ticket %s → %d PR(s)", ctx.issue_id, len(prs))
        return ctx, envelope.session_id

    async def _open_prs(self, ctx: _Pipeline, built: list[RepoClone]) -> list[github.PullRequest]:
        """Push and open a PR for each repo the engineer changed, record it, and post all the links
        to the ticket as one comment (before review — so they're on the work item regardless of how
        the review turns out)."""
        prs: list[github.PullRequest] = []
        for target in built:
            await self._volumes.push(
                ticket_id=ctx.issue_id, key=target.key, github_repo=target.github_repo
            )
            pr = await ctx.github.create_pull_request(
                target.github_repo,
                head=f"ticket/{ctx.issue_id}",
                base=target.base_branch,
                title=_issue_title(ctx.issue),
                body=_pr_body(ctx.issue_id, ctx.issue),
            )
            await self._tickets.add_pr(
                ctx.issue_id,
                repo_key=target.key,
                github_repo=target.github_repo,
                pr_number=pr.number,
                pr_url=pr.html_url,
            )
            await self._emit(
                ctx.job_id,
                f"Opened PR #{pr.number} in {target.key}",
                ticket_id=ctx.issue_id,
                level="success",
            )
            prs.append(pr)
        # Keep the ticket's primary PR fields populated (UI/notify) — the first opened PR.
        await self._tickets.set_pr(ctx.issue_id, prs[0].number, prs[0].html_url)
        links = "\n\n".join(f"{t.key}: {pr.html_url}" for t, pr in zip(built, prs, strict=True))
        await self._post_comment(ctx, f"Pull request(s) opened:\n\n{links}")
        return prs

    async def _park(
        self,
        ctx: _Pipeline,
        status: str,
        comment: str,
        *,
        workflow_state: WorkflowState = WorkflowState.BLOCKED,
    ) -> None:
        """Stop the pipeline for a ticket that needs a human: mirror it onto the Plane board's
        blocked column, post the reason as a comment, and set a terminal local status (not `done`,
        so blocked dependents stay blocked). Best-effort on the Plane calls — a hiccup shouldn't
        turn a valid park into a failure."""
        await self._tickets.set_status(ctx.issue_id, status)
        await self._move_plane_state(ctx, workflow_state)
        await self._post_comment(ctx, comment)
        await self._emit(
            ctx.job_id, f"Parked for a human ({status})", ticket_id=ctx.issue_id, level="warning"
        )
        logger.info("Ticket %s parked (%s)", ctx.issue_id, status)

    async def _emit(
        self, job_id: int, message: str, *, ticket_id: str = "", level: str = "info"
    ) -> None:
        """Record a pipeline-step event for the job's console timeline. Best-effort — never let a
        failed event write break a dispatch."""
        try:
            await job_events.record_event(
                self._sessionmaker,
                job_id=job_id,
                message=message,
                ticket_id=ticket_id,
                level=level,
            )
        except Exception:
            logger.warning("Failed to record job event", exc_info=True)

    async def _post_comment(self, ctx: _Pipeline, comment: str) -> None:
        """Best-effort Plane comment — a Plane hiccup must never turn valid work into a failure."""
        try:
            await ctx.plane.post_comment(ctx.project_id, ctx.issue_id, _html_paragraphs(comment))
        except plane.PlaneError as exc:
            logger.warning("Could not post comment to %s: %s", ctx.issue_id, exc.detail)

    async def _resume_context(self, ctx: _Pipeline, existing: Ticket | None) -> _Resume | None:
        """A parked ticket moved back to ready_for_dev resumes with memory — rather than a fresh
        clone — when it has a saved engineer session, a resumable parked status, and its volumes are
        still present. Otherwise return None and the caller builds fresh."""
        if existing is None or not existing.engineer_session_id:
            return None
        if existing.agent_status not in _RESUMABLE_PARKED:
            return None
        if not await self._volumes.has_session(ticket_id=ctx.issue_id):
            logger.info("Ticket %s parked but its volumes are gone; rebuilding fresh", ctx.issue_id)
            return None
        logger.info(
            "Resuming parked ticket %s from session %s",
            ctx.issue_id,
            existing.engineer_session_id,
        )
        return _Resume(
            session_id=existing.engineer_session_id,
            conversation=await self._recent_comments(ctx),
        )

    async def _recent_comments(self, ctx: _Pipeline, limit: int = 12) -> str:
        """The tail of the ticket's Plane comment thread as plain text, so the resumed engineer sees
        the human's answer to the question it paused on. Best-effort."""
        try:
            comments = await ctx.plane.list_comments(ctx.project_id, ctx.issue_id)
        except plane.PlaneError as exc:
            logger.warning("Could not fetch comments for %s: %s", ctx.issue_id, exc.detail)
            return ""
        texts = []
        for comment in comments[-limit:]:
            raw = comment.get("comment_stripped") or _HTML_TAG.sub(
                "", str(comment.get("comment_html", ""))
            )
            if text := str(raw).strip():
                texts.append(text)
        return "\n\n".join(texts)

    async def _review_loop(self, ctx: _Pipeline, session_id: str) -> None:
        """Run reviewer + QA; on any fail, feed findings back to the engineer and re-review until
        both pass (→ ready_for_approval) or the engineer's diff stops changing (→ stalled)."""
        last_hash = await self._volumes.diff_hash(ticket_id=ctx.issue_id, targets=ctx.built)
        await self._tickets.set_diff_hash(ctx.issue_id, last_hash)
        while True:
            verdicts = await self._run_checkers(ctx)
            if all(verdict.passed for _, verdict in verdicts):
                await self._set_state(ctx, "ready_for_approval", WorkflowState.READY_FOR_APPROVAL)
                await self._emit(
                    ctx.job_id,
                    "Review passed → ready for approval",
                    ticket_id=ctx.issue_id,
                    level="success",
                )
                await self._notify(
                    ctx.cfg, f"Ticket {ctx.issue_id} ready for approval: {ctx.pr_url}"
                )
                logger.info("Ticket %s passed review → ready_for_approval", ctx.issue_id)
                return
            await self._post_findings(ctx, verdicts)
            round_no = await self._tickets.bump_loop_round(ctx.issue_id)
            await self._set_state(ctx, "changes_requested", WorkflowState.CHANGES_REQUESTED)
            await self._emit(
                ctx.job_id,
                f"Review requested changes → resuming engineer (round {round_no})",
                ticket_id=ctx.issue_id,
                level="warning",
            )
            await self._resume_engineer(ctx, session_id, verdicts, round_no)
            for target in ctx.built:
                await self._volumes.push(
                    ticket_id=ctx.issue_id, key=target.key, github_repo=target.github_repo
                )
            new_hash = await self._volumes.diff_hash(ticket_id=ctx.issue_id, targets=ctx.built)
            if new_hash == last_hash:  # engineer resume produced an identical diff → stalled
                await self._tickets.set_status(ctx.issue_id, "stalled")
                await self._emit(
                    ctx.job_id,
                    f"Stalled at round {round_no} — the engineer produced no new changes",
                    ticket_id=ctx.issue_id,
                    level="warning",
                )
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
                    job_id=ctx.job_id,
                    prompt=await self._checker_prompt(role, ctx),
                    model=self._model(ctx.cfg, role),
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
                job_id=ctx.job_id,
                prompt=await self._prompts.render(
                    "engineer_followup",
                    ticket_id=ctx.issue_id,
                    findings=_format_findings(verdicts),
                ),
                model=self._model(ctx.cfg, AgentRole.ENGINEER),
                resume_session_id=session_id,
                loop_round=round_no,
            )
        )

    async def _engineer_prompt(
        self, issue_id: str, issue: dict[str, object], targets: list[RepoClone]
    ) -> str:
        return await self._prompts.render(
            "engineer",
            ticket_id=issue_id,
            title=_issue_title(issue),
            body=_issue_body(issue),
            repos=_repos_block(targets),
        )

    async def _planner_prompt(self, epic: dict[str, object], repos: list[RepoMappingView]) -> str:
        repo_lines = "\n".join(
            f"- `{r.key}` → {r.github_repo} (base branch `{r.base_branch}`)" for r in repos
        )
        return await self._prompts.render(
            "planner", title=_issue_title(epic), body=_issue_body(epic), repos=repo_lines
        )

    async def _checker_prompt(self, role: AgentRole, ctx: _Pipeline) -> str:
        return await self._prompts.render(
            role.value,
            ticket_id=ctx.issue_id,
            title=_issue_title(ctx.issue),
            body=_issue_body(ctx.issue),
            repos=_repos_block(ctx.built or ctx.targets),
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

    def _model(self, cfg: dict[str, str], role: AgentRole) -> str:
        return cfg.get(MODEL_SETTING[role], _DEFAULT_MODEL.get(role, "claude-haiku-4-5"))

    async def _set_state(
        self, ctx: _Pipeline, local_status: str, workflow_state: WorkflowState
    ) -> None:
        """Advance the ticket's local status and mirror it onto the Plane board. Best-effort: an
        unmapped state or a Plane error is logged, and the dispatch still continues."""
        await self._tickets.set_status(ctx.issue_id, local_status)
        await self._move_plane_state(ctx, workflow_state)

    async def _move_plane_state(self, ctx: _Pipeline, workflow_state: WorkflowState) -> None:
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

    async def _route_repos(
        self,
        cfg: dict[str, str],
        project_id: str,
        issue: dict[str, Any],
        existing: Ticket | None,
    ) -> list[RepoMappingView]:
        """The repo(s) a ticket needs work in. A planner-assigned `target_repo` wins; a single-repo
        project is trivial; otherwise the router decides from the ticket text + each repo's README
        (fetched conductor-side). Always returns at least one repo."""
        repos = await self._mappings.list_repos(project_id)
        if not repos:
            raise RuntimeError(f"project {project_id} has no mapped repo")
        if existing and existing.target_repo:
            return [r for r in repos if r.key == existing.target_repo] or repos[:1]
        if len(repos) == 1:
            return repos[:1]
        gh = self._github_factory(cfg)
        catalog = [(r.key, await gh.get_readme(r.github_repo)) for r in repos]
        ticket_text = f"{_issue_title(issue)}\n\n{_issue_body(issue)}"
        keys = await self._route_fn(cfg, ticket=ticket_text, catalog=catalog)
        return [r for r in repos if r.key in keys] or repos[:1]

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


def _repos_block(targets: list[RepoClone]) -> str:
    return "\n".join(
        f"- `/work/{t.key}` — {t.github_repo} (branch `{t.base_branch}`)" for t in targets
    )


def _pr_body(issue_id: str, issue: dict[str, object]) -> str:
    body = _issue_body(issue)
    return f"Automated implementation of ticket `{issue_id}`.\n\n{body}".rstrip()


_NEEDS_INPUT_MARKER = "NEEDS_INPUT:"


def _needs_input(result: str) -> str | None:
    """If the engineer signalled it can't proceed without a human decision (a `NEEDS_INPUT:` line,
    per engineer.md), return the question; else None."""
    for line in result.splitlines():
        stripped = line.strip()
        if stripped.startswith(_NEEDS_INPUT_MARKER):
            return stripped[len(_NEEDS_INPUT_MARKER) :].strip() or "(no question given)"
    return None


_URL_RE = re.compile(r"https?://[^\s<]+")


def _html_paragraphs(text: str) -> str:
    return "".join(f"<p>{_linkify(block)}</p>" for block in text.split("\n\n") if block.strip())


def _linkify(block: str) -> str:
    """Escape a text block for Plane's `comment_html`, turning bare URLs into clickable `<a>` links
    (so PR links posted to a ticket are actually clickable)."""
    from html import escape

    parts: list[str] = []
    last = 0
    for match in _URL_RE.finditer(block):
        parts.append(escape(block[last : match.start()]))
        url = match.group(0)
        parts.append(f'<a href="{escape(url)}" target="_blank">{escape(url)}</a>')
        last = match.end()
    parts.append(escape(block[last:]))
    return "".join(parts)


def _plan_issue_html(planned: contracts.PlannedTicket) -> str:
    html = f"<p>{planned.body}</p>"
    if planned.acceptance_criteria:
        html += f"<p><b>Acceptance criteria</b></p><p>{planned.acceptance_criteria}</p>"
    return html


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
