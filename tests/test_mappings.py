from pathlib import Path

from conductor.mappings import MappingStore
from conductor.models import WorkflowState


async def test_set_get_and_delete_project(mappings: MappingStore) -> None:
    await mappings.set_project("p1", repo="izzy/chess", base_branch="dev")
    row = await mappings.get_project("p1")
    assert row is not None
    assert row.repo == "izzy/chess"
    assert row.base_branch == "dev"
    assert await mappings.delete_project("p1") is True
    assert await mappings.get_project("p1") is None
    assert await mappings.delete_project("p1") is False


async def test_delete_project_cascades_state_mappings(mappings: MappingStore) -> None:
    await mappings.set_project("p1", repo="izzy/chess")
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s1")
    assert await mappings.delete_project("p1") is True
    assert await mappings.list_states("p1") == []


async def test_set_project_updates_existing(mappings: MappingStore) -> None:
    await mappings.set_project("p1", repo="izzy/a")
    await mappings.set_project("p1", repo="izzy/b")
    rows = await mappings.list_projects()
    assert len(rows) == 1
    assert rows[0]["repo"] == "izzy/b"


async def test_state_mapping_upserts_by_project_and_state(
    mappings: MappingStore,
) -> None:
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s1")
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s2")
    states = await mappings.list_states("p1")
    assert states == [{"workflow_state": "in_review", "plane_state_id": "s2"}]


async def test_import_targets_seeds_once(
    mappings: MappingStore, tmp_path: Path
) -> None:
    f = tmp_path / "targets.yml"
    f.write_text(
        "targets:\n"
        "  - workspace: dem\n"
        "    project_id: p1\n"
        "    github_repo: izzy/chess\n"
        "    base_branch: main\n"
    )
    assert await mappings.import_targets(f) == 1
    # DB wins: a second import without reseed skips the existing row.
    await mappings.set_project("p1", repo="izzy/changed")
    assert await mappings.import_targets(f) == 0
    row = await mappings.get_project("p1")
    assert row is not None and row.repo == "izzy/changed"
    assert await mappings.import_targets(f, reseed=True) == 1
    row = await mappings.get_project("p1")
    assert row is not None and row.repo == "izzy/chess"
