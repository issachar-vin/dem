from pathlib import Path

import pytest
from pydantic import ValidationError

from conductor.targets import DuplicateProjectError, load_targets


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_targets(tmp_path / "nope.yml") == {}


def test_loads_and_keys_by_project_id(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "targets.yml",
        """
targets:
  - workspace: dem
    project_id: proj-1
    github_repo: izzy/chess-bro
  - workspace: dem
    project_id: proj-2
    github_repo: izzy/other
    base_branch: develop
""",
    )
    targets = load_targets(f)
    assert set(targets) == {"proj-1", "proj-2"}
    assert targets["proj-1"].github_repo == "izzy/chess-bro"
    assert targets["proj-2"].base_branch == "develop"


def test_duplicate_project_id_rejected(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "targets.yml",
        """
targets:
  - workspace: dem
    project_id: dup
    github_repo: izzy/a
  - workspace: dem
    project_id: dup
    github_repo: izzy/b
""",
    )
    with pytest.raises(DuplicateProjectError, match="dup"):
        load_targets(f)


def test_bad_repo_form_rejected(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "targets.yml",
        """
targets:
  - workspace: dem
    project_id: proj-1
    github_repo: not-a-valid-repo
""",
    )
    with pytest.raises(ValidationError, match="owner/name"):
        load_targets(f)
