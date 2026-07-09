from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.crypto import SecretBox
from conductor.models import ProjectMapping, RepoMapping, StateMapping, WorkflowState
from conductor.targets import load_targets


class RepoMappingView(BaseModel):
    key: str
    github_repo: str
    base_branch: str
    source: str


class StateMappingView(BaseModel):
    workflow_state: str
    plane_state_id: str


class ProjectMappingView(BaseModel):
    """Read model for a project mapping. Exposes only whether a webhook secret is set, never the
    secret itself — the plaintext is reachable through get_webhook_secret for internal use only."""

    plane_project_id: str
    enabled: bool
    has_webhook_secret: bool
    source: str
    repos: list[RepoMappingView]


class MappingStore:
    """DB-backed project↔repos and canonical-state↔Plane-state mappings. A project owns many
    repos and one (encrypted) webhook secret shared across them."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox) -> None:
        self._sessionmaker = sessionmaker
        self._box = box

    # ── project mappings ─────────────────────────────────────────────────────
    async def get_project(self, project_id: str) -> ProjectMapping | None:
        async with self._sessionmaker() as session:
            return await session.get(ProjectMapping, project_id)

    async def list_projects(self) -> list[ProjectMappingView]:
        async with self._sessionmaker() as session:
            projects = (await session.execute(select(ProjectMapping))).scalars().all()
            repos = (await session.execute(select(RepoMapping))).scalars().all()
        by_project: dict[str, list[RepoMappingView]] = {}
        for r in repos:
            by_project.setdefault(r.plane_project_id, []).append(
                RepoMappingView(
                    key=r.key,
                    github_repo=r.github_repo,
                    base_branch=r.base_branch,
                    source=r.source,
                )
            )
        return [
            ProjectMappingView(
                plane_project_id=p.plane_project_id,
                enabled=p.enabled,
                has_webhook_secret=bool(p.webhook_secret),
                source=p.source,
                repos=by_project.get(p.plane_project_id, []),
            )
            for p in projects
        ]

    async def set_project(
        self,
        project_id: str,
        *,
        enabled: bool = False,
        webhook_secret: str | None = None,
        source: str = "ui",
    ) -> None:
        """Upsert a project's project-level fields. `webhook_secret` is only written when a
        non-None value is passed, so toggling `enabled` never wipes an existing secret."""
        async with self._sessionmaker() as session:
            row = await session.get(ProjectMapping, project_id)
            if row is None:
                row = ProjectMapping(plane_project_id=project_id)
                session.add(row)
            row.enabled = enabled
            row.source = source
            if webhook_secret is not None:
                row.webhook_secret = self._box.encrypt(webhook_secret)
            await session.commit()

    async def get_webhook_secret(self, project_id: str) -> str | None:
        async with self._sessionmaker() as session:
            row = await session.get(ProjectMapping, project_id)
            if row is None or row.webhook_secret is None:
                return None
            return self._box.decrypt(row.webhook_secret)

    async def delete_project(self, project_id: str) -> bool:
        async with self._sessionmaker() as session:
            row = await session.get(ProjectMapping, project_id)
            if row is None:
                return False
            await session.execute(
                delete(RepoMapping).where(RepoMapping.plane_project_id == project_id)
            )
            await session.execute(
                delete(StateMapping).where(StateMapping.plane_project_id == project_id)
            )
            await session.delete(row)
            await session.commit()
            return True

    # ── repo mappings ────────────────────────────────────────────────────────
    async def list_repos(self, project_id: str) -> list[RepoMappingView]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(RepoMapping).where(RepoMapping.plane_project_id == project_id)
                )
            ).scalars()
            return [
                RepoMappingView(
                    key=r.key,
                    github_repo=r.github_repo,
                    base_branch=r.base_branch,
                    source=r.source,
                )
                for r in rows
            ]

    async def set_repo(
        self,
        project_id: str,
        key: str,
        *,
        github_repo: str,
        base_branch: str = "main",
        source: str = "ui",
    ) -> None:
        async with self._sessionmaker() as session:
            existing = (
                await session.execute(
                    select(RepoMapping).where(
                        RepoMapping.plane_project_id == project_id,
                        RepoMapping.key == key,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    RepoMapping(
                        plane_project_id=project_id,
                        key=key,
                        github_repo=github_repo,
                        base_branch=base_branch,
                        source=source,
                    )
                )
            else:
                existing.github_repo = github_repo
                existing.base_branch = base_branch
                existing.source = source
            await session.commit()

    async def delete_repo(self, project_id: str, key: str) -> bool:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(RepoMapping).where(
                        RepoMapping.plane_project_id == project_id,
                        RepoMapping.key == key,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    # ── state mappings ───────────────────────────────────────────────────────
    async def list_states(self, project_id: str) -> list[StateMappingView]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(StateMapping).where(StateMapping.plane_project_id == project_id)
                )
            ).scalars()
            return [
                StateMappingView(workflow_state=r.workflow_state, plane_state_id=r.plane_state_id)
                for r in rows
            ]

    async def set_state(
        self, project_id: str, workflow_state: WorkflowState, plane_state_id: str
    ) -> None:
        async with self._sessionmaker() as session:
            existing = (
                await session.execute(
                    select(StateMapping).where(
                        StateMapping.plane_project_id == project_id,
                        StateMapping.workflow_state == workflow_state.value,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    StateMapping(
                        plane_project_id=project_id,
                        workflow_state=workflow_state.value,
                        plane_state_id=plane_state_id,
                    )
                )
            else:
                existing.plane_state_id = plane_state_id
            await session.commit()

    async def delete_states(self, project_id: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                delete(StateMapping).where(StateMapping.plane_project_id == project_id)
            )
            await session.commit()

    # ── seed import (targets.yml → project + repo mappings, seed-once) ─────────
    async def import_targets(self, path: Path, *, reseed: bool = False) -> int:
        imported = 0
        for project_id, target in load_targets(path).items():
            if not reseed and await self.get_project(project_id) is not None:
                continue
            await self.set_project(
                project_id,
                enabled=target.enabled,
                webhook_secret=target.webhook_secret,
                source="seed",
            )
            for repo in target.repos:
                await self.set_repo(
                    project_id,
                    repo.key,
                    github_repo=repo.github_repo,
                    base_branch=repo.base_branch,
                    source="seed",
                )
            imported += 1
        return imported
