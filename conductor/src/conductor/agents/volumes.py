"""Per-ticket Docker volume lifecycle. Each ticket gets two named volumes — `psa-repo-<id>` (the
clone, at /work) and `psa-claude-<id>` (Claude Code session state, for --resume). Creating the repo
volume is credentialed (git clone with the machine token) and so is done here by the conductor, not
inside the agent — the token never enters an agent container. The clone helper also strips the token
from the stored remote and chowns /work to the agent user (a fresh named volume mounts root-owned;
without the chown the non-root agent can't write it — the Part 1 carry-in fix)."""

import asyncio
import logging
from collections.abc import Callable

from conductor.agents.dockerctl import DockerClient, DockerFactory, run_container
from conductor.catalog import DEFAULT_AGENT_IMAGE
from conductor.github import GitHubClient, GitHubUser, client_from_resolved
from conductor.store import ConfigStore

logger = logging.getLogger("conductor")


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

    async def prepare(self, *, ticket_id: str, github_repo: str, base_branch: str) -> None:
        """Create both volumes and populate the repo volume with a fresh clone on branch
        `ticket/<id>`, git identity configured from the token's own account."""
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
            command=[_clone_script(github_repo, base_branch, ticket_id, identity)],
            name=f"psa-clone-{ticket_id}",
            environment={"CLONE_TOKEN": cfg.get("github_token", "")},
            volumes={repo_vol: "/work"},
            user="root",
        )

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


def _clone_script(github_repo: str, base_branch: str, ticket_id: str, identity: GitHubUser) -> str:
    # Token is passed via $CLONE_TOKEN (env), not the command line, then stripped from the stored
    # remote so it never persists in a volume the agent can read.
    return "\n".join(
        [
            "set -euo pipefail",
            # The volume mounts owned by the image's `agent` user, but this helper runs as root to
            # clone + chown. Since git 2.35.2 that ownership mismatch trips "dubious ownership" and
            # aborts, even for root — so trust /work explicitly. The final chown hands the tree to
            # the agent, whose own git then sees a matching owner and needs no such exception.
            "git config --global --add safe.directory /work",
            f'git clone --depth 50 --branch "{base_branch}" '
            f'"https://x-access-token:${{CLONE_TOKEN}}@github.com/{github_repo}.git" /work',
            "cd /work",
            f'git remote set-url origin "https://github.com/{github_repo}.git"',
            f'git checkout -b "ticket/{ticket_id}"',
            f'git config user.name "{identity.git_name}"',
            f'git config user.email "{identity.git_email}"',
            "chown -R agent:agent /work",
        ]
    )
