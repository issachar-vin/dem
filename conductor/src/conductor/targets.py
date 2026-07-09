from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class RepoTarget(BaseModel):
    key: str
    github_repo: str
    base_branch: str = "main"

    @field_validator("github_repo")
    @classmethod
    def _validate_repo(cls, v: str) -> str:
        if v.count("/") != 1 or v.startswith("/") or v.endswith("/"):
            raise ValueError(f"github_repo must be in 'owner/name' form, got {v!r}")
        return v


class Target(BaseModel):
    workspace: str
    project_id: str
    enabled: bool = False
    webhook_secret: str | None = None
    repos: list[RepoTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_repo_keys(self) -> "Target":
        keys = [r.key for r in self.repos]
        if len(keys) != len(set(keys)):
            raise ValueError(f"repo keys must be unique within project {self.project_id}")
        return self


class TargetsFile(BaseModel):
    targets: list[Target] = Field(default_factory=list)


class DuplicateProjectError(ValueError):
    pass


def parse_targets(text: str) -> dict[str, Target]:
    """Parse targets.yml content into a project_id -> Target map."""
    data = yaml.safe_load(text) or {}
    parsed = TargetsFile.model_validate(data)

    by_project: dict[str, Target] = {}
    for target in parsed.targets:
        if target.project_id in by_project:
            raise DuplicateProjectError(
                f"project_id {target.project_id} is mapped to more than one repo"
            )
        by_project[target.project_id] = target
    return by_project


def load_targets(path: Path) -> dict[str, Target]:
    """Load targets.yml into a project_id -> Target map. Missing file yields an empty map."""
    if not path.exists():
        return {}
    return parse_targets(path.read_text())


def dump_targets(targets: dict[str, Target]) -> str:
    """Serialize a project_id -> Target map back to targets.yml content (the inverse of
    parse_targets). Includes plaintext webhook secrets — handle carefully."""
    doc = TargetsFile(targets=list(targets.values()))
    return yaml.safe_dump(doc.model_dump(exclude_none=True), sort_keys=False)
