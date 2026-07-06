from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class Target(BaseModel):
    workspace: str
    project_id: str
    github_repo: str
    base_branch: str = "main"

    # Optional per-repo overrides; fall back to the conductor's global settings.
    agent_image: str | None = None
    model_engineer: str | None = None
    model_planner: str | None = None
    model_reviewer: str | None = None
    model_qa: str | None = None

    @field_validator("github_repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        if v.count("/") != 1 or v.startswith("/") or v.endswith("/"):
            raise ValueError(f"github_repo must be in 'owner/name' form, got {v!r}")
        return v


class TargetsFile(BaseModel):
    targets: list[Target] = Field(default_factory=list)


class DuplicateProjectError(ValueError):
    pass


def load_targets(path: Path) -> dict[str, Target]:
    """Load targets.yml into a project_id -> Target map. Missing file yields an empty map."""
    if not path.exists():
        return {}

    data = yaml.safe_load(path.read_text()) or {}
    parsed = TargetsFile.model_validate(data)

    by_project: dict[str, Target] = {}
    for target in parsed.targets:
        if target.project_id in by_project:
            raise DuplicateProjectError(
                f"project_id {target.project_id} is mapped to more than one repo in {path}"
            )
        by_project[target.project_id] = target
    return by_project
