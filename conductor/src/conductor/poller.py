"""GitHub poll mode: the webhook-free intake path. On an interval it reads each mapped repo's open
PRs and enqueues a Job whenever a PR's state changes, so a deployment without a public URL still
notices review activity. Same Job contract as the webhook handler — intake only, no dispatch."""

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import github
from conductor.jobs import enqueue_job
from conductor.mappings import MappingStore
from conductor.store import ConfigStore

logger = logging.getLogger("conductor")

_DEFAULT_INTERVAL = 60


def _pr_signature(pr: dict[str, Any]) -> str:
    """A cheap fingerprint of the PR state we care about; any change re-enqueues."""
    return f"{pr.get('state', '')}:{bool(pr.get('merged_at'))}:{pr.get('updated_at', '')}"


class GitHubPoller:
    def __init__(
        self,
        *,
        store: ConfigStore,
        mappings: MappingStore,
        sessionmaker: async_sessionmaker[AsyncSession],
        client_factory: Any = github.client_from_resolved,
    ) -> None:
        self._store = store
        self._mappings = mappings
        self._sessionmaker = sessionmaker
        self._client_factory = client_factory
        # repo full_name → {pr_number: signature}. Presence of a repo key means it is baselined,
        # so the first sweep records state without enqueuing (no startup storm of stale PRs).
        self._seen: dict[str, dict[int, str]] = {}

    async def poll_once(self) -> int:
        resolved = await self._store.resolved()
        if not resolved.get("github_token"):
            return 0
        client = self._client_factory(resolved)
        enqueued = 0
        for project in await self._mappings.list_projects():
            if not project.enabled:
                continue
            for repo in project.repos:
                enqueued += await self._poll_repo(
                    client, project.plane_project_id, repo.github_repo
                )
        return enqueued

    async def _poll_repo(self, client: Any, project_id: str, repo: str) -> int:
        try:
            prs = await client.list_pull_requests(repo)
        except github.GitHubError as exc:
            logger.warning("Poll of %s failed: %s", repo, exc.detail)
            return 0

        baseline = repo not in self._seen
        current: dict[int, str] = {}
        enqueued = 0
        for pr in prs:
            number = pr.get("number")
            if not isinstance(number, int):
                continue
            signature = _pr_signature(pr)
            current[number] = signature
            if baseline or self._seen[repo].get(number) == signature:
                continue
            job = await enqueue_job(
                self._sessionmaker,
                source="github",
                event_type="poll.pull_request",
                payload={
                    "project_id": project_id,
                    "repo": repo,
                    "pr_number": number,
                    "state": pr.get("state", ""),
                    "merged": bool(pr.get("merged_at")),
                },
            )
            if job is not None:
                enqueued += 1
        self._seen[repo] = current
        return enqueued

    async def run(self) -> None:
        while True:
            try:
                count = await self.poll_once()
                if count:
                    logger.info("Poll enqueued %d job(s)", count)
            except Exception:  # a poll error must not kill the loop
                logger.exception("Poll cycle failed")
            await asyncio.sleep(await self._interval())

    async def _interval(self) -> float:
        resolved = await self._store.resolved()
        try:
            return max(1.0, float(resolved.get("github_poll_interval_seconds", _DEFAULT_INTERVAL)))
        except ValueError:
            return float(_DEFAULT_INTERVAL)


async def start_if_enabled(
    *,
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None] | None:
    """Launch the poll loop iff GITHUB_EVENT_MODE=poll. Returns the task so the lifespan can cancel
    it on shutdown; None in webhook mode."""
    resolved = await store.resolved()
    if resolved.get("github_event_mode") != "poll":
        return None
    poller = GitHubPoller(store=store, mappings=mappings, sessionmaker=sessionmaker)
    task = asyncio.create_task(poller.run())
    logger.info("GitHub poll mode enabled")
    return task


async def stop(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
