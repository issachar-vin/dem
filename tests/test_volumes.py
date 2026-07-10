from conductor.agents.volumes import VolumeManager
from conductor.github import GitHubUser
from conductor.store import ConfigStore

from agents_fakes import FakeDocker


class FakeGitHub:
    def __init__(self, user: GitHubUser) -> None:
        self._user = user

    async def get_user(self) -> GitHubUser:
        return self._user


def _manager(store: ConfigStore, docker: FakeDocker, user: GitHubUser) -> VolumeManager:
    return VolumeManager(
        store=store,
        docker_factory=lambda: docker,
        github_factory=lambda _resolved: FakeGitHub(user),  # type: ignore[arg-type]
    )


async def test_prepare_creates_volumes_and_clones(store: ConfigStore) -> None:
    await store.set_secret("github_token", "ghtok")
    docker = FakeDocker()
    user = GitHubUser(login="bot", id=42, name="Bot", email=None)
    await _manager(store, docker, user).prepare(
        ticket_id="T-1", github_repo="octo/backend", base_branch="main"
    )
    assert set(docker.volumes.created) == {"psa-repo-T-1", "psa-claude-T-1"}

    image, command, kwargs = docker.containers.calls[0]
    assert kwargs["name"] == "psa-clone-T-1"
    assert kwargs["user"] == "root"
    # The agent entrypoint asserts a Claude credential; the clone helper has none, so it must
    # override the entrypoint and run the script under plain bash.
    assert kwargs["entrypoint"] == ["bash", "-c"]
    assert kwargs["environment"]["CLONE_TOKEN"] == "ghtok"
    assert kwargs["volumes"] == {"psa-repo-T-1": {"bind": "/work", "mode": "rw"}}

    script = command[0]
    assert "git config --global --add safe.directory /work" in script
    assert 'git clone --depth 50 --branch "main"' in script
    assert "octo/backend.git" in script
    assert 'git checkout -b "ticket/T-1"' in script
    assert 'git config user.email "42+bot@users.noreply.github.com"' in script
    assert "chown -R agent:agent /work" in script


async def test_clone_strips_token_from_remote(store: ConfigStore) -> None:
    await store.set_secret("github_token", "ghtok")
    docker = FakeDocker()
    user = GitHubUser(login="bot", id=1)
    await _manager(store, docker, user).prepare(
        ticket_id="T-2", github_repo="octo/ui", base_branch="dev"
    )
    script = docker.containers.calls[0][1][0]
    assert 'git remote set-url origin "https://github.com/octo/ui.git"' in script


async def test_destroy_removes_both_volumes(store: ConfigStore) -> None:
    await store.set_secret("github_token", "ghtok")
    docker = FakeDocker()
    user = GitHubUser(login="bot", id=1)
    manager = _manager(store, docker, user)
    await manager.prepare(ticket_id="T-3", github_repo="octo/ui", base_branch="main")
    await manager.destroy(ticket_id="T-3")
    assert docker.volumes.store["psa-repo-T-3"].removed is True
    assert docker.volumes.store["psa-claude-T-3"].removed is True


async def test_destroy_missing_volume_is_swallowed(store: ConfigStore) -> None:
    docker = FakeDocker()
    user = GitHubUser(login="bot", id=1)
    await _manager(store, docker, user).destroy(ticket_id="ghost")  # no volumes exist
