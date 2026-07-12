"""Per-ticket Docker volume lifecycle. Each ticket gets two named volumes — `psa-repo-<id>` (the
clone, at /work) and `psa-claude-<id>` (Claude Code session state, for --resume). Creating the repo
volume is credentialed (git clone with the machine token) and so is done here by the conductor, not
inside the agent — the token never enters an agent container. The clone helper also strips the token
from the stored remote and chowns /work to the agent user (a fresh named volume mounts root-owned;
without the chown the non-root agent can't write it — the Part 1 carry-in fix)."""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from conductor.agents.dockerctl import DockerClient, DockerFactory, run_container
from conductor.catalog import DEFAULT_AGENT_IMAGE
from conductor.github import GitHubClient, GitHubUser, client_from_resolved
from conductor.store import ConfigStore

logger = logging.getLogger("conductor")


@dataclass(frozen=True)
class RepoClone:
    """One repo to clone into a planner workspace, at `/work/<key>`."""

    key: str
    github_repo: str
    base_branch: str


class VolumeManager:
    def __init__(
        self,
        *,
        store: ConfigStore,
        docker_factory: DockerFactory,
        github_factory: Callable[[dict[str, str]], GitHubClient] = client_from_resolved,
    ) -> None:
        self._store = store
        self._docker_factory = docker_factory
        self._github_factory = github_factory

    async def prepare(self, *, ticket_id: str, targets: Sequence[RepoClone]) -> None:
        """Create both volumes and clone each target repo into `/work/<key>` on branch
        `ticket/<id>`, git identity configured from the token's own account. A single-repo ticket is
        just the one-target case (its checkout lives at `/work/<key>`, not the repo root)."""
        cfg = await self._store.resolved()
        identity = await self._github_factory(cfg).get_user()
        client = self._docker_factory()

        repo_vol = f"psa-repo-{ticket_id}"
        claude_vol = f"psa-claude-{ticket_id}"
        # Idempotent: a prior aborted dispatch can leave a partial clone volume, and `git clone`
        # then fails on a non-empty /work. Clear any stale volumes so prepare starts clean. (This is
        # the initial-dispatch setup; the Phase-5 review loop reuses the volume via resume instead.)
        await self._remove_volumes(client, repo_vol, claude_vol)
        await asyncio.to_thread(client.volumes.create, repo_vol)
        await asyncio.to_thread(client.volumes.create, claude_vol)

        await run_container(
            client,
            image=cfg.get("agent_image") or DEFAULT_AGENT_IMAGE,
            # Override the agent entrypoint: it asserts a Claude credential before running anything,
            # but this helper only clones (no Claude creds, by design), so it must not run it.
            entrypoint=["bash", "-c"],
            command=[_clone_script(targets, ticket_id, identity)],
            name=f"psa-clone-{ticket_id}",
            environment={"CLONE_TOKEN": cfg.get("github_token", "")},
            volumes={repo_vol: "/work"},
            user="root",
        )

    async def prepare_planner(self, *, epic_id: str, repos: Sequence[RepoClone]) -> None:
        """Populate the planner's `psa-repo-<epic_id>` volume with a read-only clone of each of the
        project's repos at `/work/<key>` so the planner can scope tickets against the real codebase.
        Credentialed (machine token), so it runs here not in the agent — like the engineer clone."""
        cfg = await self._store.resolved()
        client = self._docker_factory()
        repo_vol = f"psa-repo-{epic_id}"
        claude_vol = f"psa-claude-{epic_id}"
        await self._remove_volumes(client, repo_vol, claude_vol)
        await asyncio.to_thread(client.volumes.create, repo_vol)
        await asyncio.to_thread(client.volumes.create, claude_vol)
        await run_container(
            client,
            image=cfg.get("agent_image") or DEFAULT_AGENT_IMAGE,
            entrypoint=["bash", "-c"],
            command=[_planner_clone_script(repos)],
            name=f"psa-clone-{epic_id}",
            environment={"CLONE_TOKEN": cfg.get("github_token", "")},
            volumes={repo_vol: "/work"},
            user="root",
        )

    async def push(self, *, ticket_id: str, key: str, github_repo: str) -> None:
        """Push one repo's `ticket/<id>` branch to origin, from its `/work/<key>` checkout.
        Credentialed (the token is stripped from the volume's stored remote), so it runs
        conductor-side in a root helper — keeping the token out of the agent container."""
        cfg = await self._store.resolved()
        client = self._docker_factory()
        await run_container(
            client,
            image=cfg.get("agent_image") or DEFAULT_AGENT_IMAGE,
            entrypoint=["bash", "-c"],
            command=[_push_script(github_repo, ticket_id, key)],
            name=f"psa-push-{key}-{ticket_id}",
            environment={"CLONE_TOKEN": cfg.get("github_token", "")},
            volumes={f"psa-repo-{ticket_id}": "/work"},
            user="root",
        )

    async def commit_count(self, *, ticket_id: str, key: str, base_branch: str) -> int:
        """Number of commits the engineer added on one repo's `ticket/<id>` branch over its base.
        Zero means no work in that repo — so no PR is opened for it (and if *every* repo is zero,
        the scheduler parks the ticket)."""
        cfg = await self._store.resolved()
        client = self._docker_factory()
        stdout = await run_container(
            client,
            image=cfg.get("agent_image") or DEFAULT_AGENT_IMAGE,
            entrypoint=["bash", "-c"],
            command=[_commit_count_script(base_branch, key)],
            name=f"psa-count-{key}-{ticket_id}",
            volumes={f"psa-repo-{ticket_id}": "/work"},
            user="root",
        )
        try:
            return int(stdout.strip() or "0")
        except ValueError:
            return 0

    async def diff_hash(self, *, ticket_id: str, targets: Sequence[RepoClone]) -> str:
        """sha256 over every target repo's `git diff <base>...HEAD`. The stall detector compares
        this across rounds — an identical hash twice means the engineer's resume produced no new
        work in any repo."""
        cfg = await self._store.resolved()
        client = self._docker_factory()
        stdout = await run_container(
            client,
            image=cfg.get("agent_image") or DEFAULT_AGENT_IMAGE,
            entrypoint=["bash", "-c"],
            command=[_diff_hash_script(targets)],
            name=f"psa-diff-{ticket_id}",
            volumes={f"psa-repo-{ticket_id}": "/work"},
            user="root",
        )
        return stdout.strip()

    async def has_session(self, *, ticket_id: str) -> bool:
        """Whether both of a ticket's volumes survive — the repo working tree and the Claude session
        state that `--resume` replays from. Gates whether a parked ticket can resume with its memory
        instead of being rebuilt from a fresh clone."""
        client = self._docker_factory()

        def _both_exist() -> bool:
            for name in (f"psa-repo-{ticket_id}", f"psa-claude-{ticket_id}"):
                try:
                    client.volumes.get(name)
                except Exception:  # docker NotFound (or any lookup failure) → treat as absent
                    return False
            return True

        return await asyncio.to_thread(_both_exist)

    async def destroy(self, *, ticket_id: str) -> None:
        client = self._docker_factory()
        await self._remove_volumes(client, f"psa-repo-{ticket_id}", f"psa-claude-{ticket_id}")

    async def _remove_volumes(self, client: DockerClient, *names: str) -> None:
        for name in names:
            try:
                volume = await asyncio.to_thread(client.volumes.get, name)
                await asyncio.to_thread(volume.remove, force=True)
            except Exception:  # best-effort; a missing volume is not an error
                logger.debug("Volume %s not removed (likely absent)", name)


def _push_script(github_repo: str, ticket_id: str, key: str) -> str:
    # The stored remote is token-stripped (see _clone_script), so push to an explicit tokened URL
    # rather than `origin`; the token stays in $CLONE_TOKEN and never lands in the volume.
    return "\n".join(
        [
            "set -euo pipefail",
            "git config --global --add safe.directory '*'",
            f"cd /work/{key}",
            f'git push "https://x-access-token:${{CLONE_TOKEN}}@github.com/{github_repo}.git" '
            f'"ticket/{ticket_id}"',
        ]
    )


def _planner_clone_script(repos: Sequence[RepoClone]) -> str:
    lines = ["set -euo pipefail", "git config --global --add safe.directory '*'"]
    for repo in repos:
        dest = f"/work/{repo.key}"
        lines += [
            f'git clone --depth 1 --branch "{repo.base_branch}" '
            f'"https://x-access-token:${{CLONE_TOKEN}}@github.com/{repo.github_repo}.git" "{dest}"',
            f'git -C "{dest}" remote set-url origin "https://github.com/{repo.github_repo}.git"',
        ]
    lines.append("chown -R agent:agent /work")
    return "\n".join(lines)


def _commit_count_script(base_branch: str, key: str) -> str:
    return "\n".join(
        [
            "set -euo pipefail",
            "git config --global --add safe.directory '*'",
            f"cd /work/{key}",
            f'git rev-list --count "{base_branch}..HEAD"',
        ]
    )


def _diff_hash_script(targets: Sequence[RepoClone]) -> str:
    # Concatenate every repo's diff (prefixed by key so identical diffs in different repos don't
    # collide), then hash the whole thing — the stall detector wants "did *anything* change".
    lines = ["set -euo pipefail", "git config --global --add safe.directory '*'", "{"]
    for t in targets:
        lines.append(f'echo "== {t.key} =="; git -C "/work/{t.key}" diff "{t.base_branch}...HEAD"')
    lines.append('} | sha256sum | cut -d" " -f1')
    return "\n".join(lines)


def _clone_script(targets: Sequence[RepoClone], ticket_id: str, identity: GitHubUser) -> str:
    # Token is passed via $CLONE_TOKEN (env), not the command line, then stripped from each stored
    # remote so it never persists in a volume the agent can read. Each repo lands at /work/<key> on
    # its own ticket branch; the final chown hands the whole tree to the agent user.
    lines = ["set -euo pipefail", "git config --global --add safe.directory '*'"]
    for t in targets:
        dest = f"/work/{t.key}"
        lines += [
            f'git clone --depth 50 --branch "{t.base_branch}" '
            f'"https://x-access-token:${{CLONE_TOKEN}}@github.com/{t.github_repo}.git" "{dest}"',
            f'git -C "{dest}" remote set-url origin "https://github.com/{t.github_repo}.git"',
            f'git -C "{dest}" checkout -b "ticket/{ticket_id}"',
            f'git -C "{dest}" config user.name "{identity.git_name}"',
            f'git -C "{dest}" config user.email "{identity.git_email}"',
        ]
    lines.append("chown -R agent:agent /work")
    return "\n".join(lines)
