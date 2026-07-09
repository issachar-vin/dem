from pathlib import Path

from conductor.mappings import MappingStore
from conductor.models import WorkflowState


async def test_set_get_and_delete_project(mappings: MappingStore) -> None:
    await mappings.set_project("p1", enabled=True)
    row = await mappings.get_project("p1")
    assert row is not None
    assert row.enabled is True
    assert await mappings.delete_project("p1") is True
    assert await mappings.get_project("p1") is None
    assert await mappings.delete_project("p1") is False


async def test_webhook_secret_encrypted_and_round_trips(mappings: MappingStore) -> None:
    await mappings.set_project("p1", enabled=True, webhook_secret="whsec_abc")
    row = await mappings.get_project("p1")
    assert row is not None and row.webhook_secret is not None
    assert row.webhook_secret != "whsec_abc"  # stored as ciphertext
    assert await mappings.get_webhook_secret("p1") == "whsec_abc"


async def test_set_project_keeps_secret_when_not_passed(mappings: MappingStore) -> None:
    await mappings.set_project("p1", enabled=True, webhook_secret="whsec_abc")
    await mappings.set_project(
        "p1", enabled=False
    )  # toggle without re-passing the secret
    assert await mappings.get_webhook_secret("p1") == "whsec_abc"
    row = await mappings.get_project("p1")
    assert row is not None and row.enabled is False


async def test_repo_crud_scoped_by_project(mappings: MappingStore) -> None:
    await mappings.set_project("p1", enabled=True)
    await mappings.set_repo("p1", "backend", github_repo="izzy/api", base_branch="dev")
    await mappings.set_repo("p1", "ui", github_repo="izzy/web")
    repos = await mappings.list_repos("p1")
    assert {r.key for r in repos} == {"backend", "ui"}
    backend = next(r for r in repos if r.key == "backend")
    assert backend.github_repo == "izzy/api"
    assert backend.base_branch == "dev"

    await mappings.set_repo("p1", "backend", github_repo="izzy/api2")  # upsert
    repos = await mappings.list_repos("p1")
    assert next(r for r in repos if r.key == "backend").github_repo == "izzy/api2"

    assert await mappings.delete_repo("p1", "backend") is True
    assert await mappings.delete_repo("p1", "backend") is False
    assert {r.key for r in await mappings.list_repos("p1")} == {"ui"}


async def test_list_projects_includes_repos_and_masks_secret(
    mappings: MappingStore,
) -> None:
    await mappings.set_project("p1", enabled=True, webhook_secret="whsec_abc")
    await mappings.set_repo("p1", "ui", github_repo="izzy/web")
    projects = await mappings.list_projects()
    assert len(projects) == 1
    view = projects[0]
    assert view.plane_project_id == "p1"
    assert view.enabled is True
    assert view.has_webhook_secret is True
    assert not hasattr(view, "webhook_secret")
    assert [r.key for r in view.repos] == ["ui"]


async def test_delete_project_cascades_repos_and_states(mappings: MappingStore) -> None:
    await mappings.set_project("p1", enabled=True)
    await mappings.set_repo("p1", "ui", github_repo="izzy/web")
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s1")
    assert await mappings.delete_project("p1") is True
    assert await mappings.list_repos("p1") == []
    assert await mappings.list_states("p1") == []


async def test_state_mapping_upserts_by_project_and_state(
    mappings: MappingStore,
) -> None:
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s1")
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s2")
    states = await mappings.list_states("p1")
    assert states == [{"workflow_state": "in_review", "plane_state_id": "s2"}]


async def test_import_targets_seeds_project_and_repos_once(
    mappings: MappingStore, tmp_path: Path
) -> None:
    f = tmp_path / "targets.yml"
    f.write_text(
        "targets:\n"
        "  - workspace: dem\n"
        "    project_id: p1\n"
        "    enabled: true\n"
        "    webhook_secret: whsec_seed\n"
        "    repos:\n"
        "      - key: backend\n"
        "        github_repo: izzy/api\n"
        "      - key: ui\n"
        "        github_repo: izzy/web\n"
    )
    assert await mappings.import_targets(f) == 1
    assert await mappings.get_webhook_secret("p1") == "whsec_seed"
    assert {r.key for r in await mappings.list_repos("p1")} == {"backend", "ui"}

    # DB wins: a second import without reseed skips the existing project.
    await mappings.set_project("p1", enabled=False)
    assert await mappings.import_targets(f) == 0
    row = await mappings.get_project("p1")
    assert row is not None and row.enabled is False

    assert await mappings.import_targets(f, reseed=True) == 1
    row = await mappings.get_project("p1")
    assert row is not None and row.enabled is True
