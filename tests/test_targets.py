from pathlib import Path

import pytest
from pydantic import ValidationError

from conductor.targets import DuplicateProjectError, load_targets


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_targets(tmp_path / "nope.yml") == {}


def test_loads_project_with_repos(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "targets.yml",
        """
targets:
  - workspace: dem
    project_id: proj-1
    enabled: true
    webhook_secret: whsec_1
    repos:
      - key: backend
        github_repo: izzy/chess-bro
      - key: ui
        github_repo: izzy/chess-ui
        base_branch: develop
  - workspace: dem
    project_id: proj-2
    repos:
      - key: backend
        github_repo: izzy/other
""",
    )
    targets = load_targets(f)
    assert set(targets) == {"proj-1", "proj-2"}
    p1 = targets["proj-1"]
    assert p1.enabled is True
    assert p1.webhook_secret == "whsec_1"
    assert {r.key for r in p1.repos} == {"backend", "ui"}
    assert next(r for r in p1.repos if r.key == "ui").base_branch == "develop"
    # Defaults: enabled off, no secret, base_branch main.
    p2 = targets["proj-2"]
    assert p2.enabled is False
    assert p2.webhook_secret is None
    assert p2.repos[0].base_branch == "main"


def test_duplicate_project_id_rejected(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "targets.yml",
        """
targets:
  - workspace: dem
    project_id: dup
    repos:
      - key: backend
        github_repo: izzy/a
  - workspace: dem
    project_id: dup
    repos:
      - key: backend
        github_repo: izzy/b
""",
    )
    with pytest.raises(DuplicateProjectError, match="dup"):
        load_targets(f)


def test_duplicate_repo_key_rejected(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "targets.yml",
        """
targets:
  - workspace: dem
    project_id: proj-1
    repos:
      - key: backend
        github_repo: izzy/a
      - key: backend
        github_repo: izzy/b
""",
    )
    with pytest.raises(ValidationError, match="repo keys must be unique"):
        load_targets(f)


def test_bad_repo_form_rejected(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "targets.yml",
        """
targets:
  - workspace: dem
    project_id: proj-1
    repos:
      - key: backend
        github_repo: not-a-valid-repo
""",
    )
    with pytest.raises(ValidationError, match="owner/name"):
        load_targets(f)
