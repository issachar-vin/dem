from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.github import GitHubError
from conductor.mappings import MappingStore
from conductor.poller import GitHubPoller
from conductor.models import Job
from conductor.store import ConfigStore


class FakeGitHub:
    def __init__(self, prs: list[dict[str, Any]]) -> None:
        self.prs = prs
        self.raise_error = False

    async def list_pull_requests(
        self, repo: str, *, state: str = "open"
    ) -> list[dict[str, Any]]:
        if self.raise_error:
            raise GitHubError("boom")
        return self.prs


async def _make_poller(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
    fake: FakeGitHub,
) -> GitHubPoller:
    await store.set_secret("github_token", "tok")
    await mappings.set_project("proj-1", enabled=True)
    await mappings.set_repo("proj-1", "backend", github_repo="octo/backend")
    return GitHubPoller(
        store=store,
        mappings=mappings,
        sessionmaker=sessionmaker,
        client_factory=lambda resolved: fake,
    )


async def _job_count(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker() as session:
        return (
            await session.execute(select(func.count()).select_from(Job))
        ).scalar_one()


async def test_first_poll_is_baseline_no_jobs(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    fake = FakeGitHub([{"number": 1, "state": "open", "updated_at": "t0"}])
    poller = await _make_poller(store, mappings, sessionmaker, fake)
    assert await poller.poll_once() == 0
    assert await _job_count(sessionmaker) == 0


async def test_state_change_enqueues(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    fake = FakeGitHub([{"number": 1, "state": "open", "updated_at": "t0"}])
    poller = await _make_poller(store, mappings, sessionmaker, fake)
    await poller.poll_once()  # baseline
    fake.prs = [{"number": 1, "state": "closed", "updated_at": "t1", "merged_at": "t1"}]
    assert await poller.poll_once() == 1
    async with sessionmaker() as session:
        job = (await session.execute(select(Job))).scalar_one()
    assert job.source == "github"
    assert job.event_type == "poll.pull_request"
    assert job.payload["merged"] is True


async def test_unchanged_pr_not_reenqueued(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    fake = FakeGitHub([{"number": 1, "state": "open", "updated_at": "t0"}])
    poller = await _make_poller(store, mappings, sessionmaker, fake)
    await poller.poll_once()
    assert await poller.poll_once() == 0


async def test_poll_error_is_swallowed(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    fake = FakeGitHub([])
    poller = await _make_poller(store, mappings, sessionmaker, fake)
    fake.raise_error = True
    assert await poller.poll_once() == 0


async def test_no_token_skips(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    fake = FakeGitHub([{"number": 1, "state": "open", "updated_at": "t0"}])
    poller = GitHubPoller(
        store=store,
        mappings=mappings,
        sessionmaker=sessionmaker,
        client_factory=lambda resolved: fake,
    )
    assert await poller.poll_once() == 0
