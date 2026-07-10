from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.crypto import SecretBox
from conductor.models import ProjectMapping, RepoMapping, StateMapping, WorkflowState
from conductor.targets import RepoTarget, Target, dump_targets, load_targets, parse_targets


class RepoMappingView(BaseModel):
    key: str
    github_repo: str
    base_branch: str
    icon: str | None = None
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
    icon: str | None
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
                    icon=r.icon,
                    source=r.source,
                )
            )
        return [
            ProjectMappingView(
                plane_project_id=p.plane_project_id,
                enabled=p.enabled,
                has_webhook_secret=bool(p.webhook_secret),
                icon=p.icon,
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
        icon: str | None = None,
        source: str = "ui",
    ) -> None:
        """Upsert a project's project-level fields. `webhook_secret` and `icon` are only written
        when a non-None value is passed, so toggling `enabled` never wipes either."""
        async with self._sessionmaker() as session:
            row = await session.get(ProjectMapping, project_id)
            if row is None:
                row = ProjectMapping(plane_project_id=project_id)
                session.add(row)
            row.enabled = enabled
            row.source = source
            if webhook_secret is not None:
                row.webhook_secret = self._box.encrypt(webhook_secret)
            if icon is not None:
                row.icon = icon
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

    async def get_state_id(self, project_id: str, workflow_state: WorkflowState) -> str | None:
        async with self._sessionmaker() as session:
            return (
                await session.execute(
                    select(StateMapping.plane_state_id).where(
                        StateMapping.plane_project_id == project_id,
                        StateMapping.workflow_state == workflow_state.value,
                    )
                )
            ).scalar_one_or_none()

    # ── repo mappings ────────────────────────────────────────────────────────
    async def get_project_for_repo(self, github_repo: str) -> str | None:
        """Resolve `owner/name` → the id of the Plane project that owns it. Drives the GitHub
        webhook's verify-after-lookup: the repo name in an unverified delivery is only trusted far
        enough to find whose secret to check the signature against."""
        async with self._sessionmaker() as session:
            return (
                (
                    await session.execute(
                        select(RepoMapping.plane_project_id).where(
                            RepoMapping.github_repo == github_repo
                        )
                    )
                )
                .scalars()
                .first()
            )

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
                    icon=r.icon,
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
        icon: str | None = None,
        source: str = "ui",
    ) -> None:
        """Upsert a repo. `icon` is only written when provided, so updating a repo's branch (or a
        wizard re-save) never wipes a previously picked icon; pass it to change the icon."""
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
                        icon=icon,
                        source=source,
                    )
                )
            else:
                existing.github_repo = github_repo
                existing.base_branch = base_branch
                existing.source = source
                if icon is not None:
                    existing.icon = icon
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

    # ── targets.yml import / export (project + repo mappings) ─────────────────
    async def import_targets(self, path: Path, *, reseed: bool = False) -> int:
        """Seed-once import from a targets.yml file (boot path; DB wins unless reseed)."""
        return await self._apply_targets(load_targets(path), reseed=reseed, source="seed")

    async def import_targets_text(self, text: str, *, reseed: bool = True) -> int:
        """Import targets.yml content supplied through the UI. Defaults to reseed so an explicit
        upload actually applies over existing mappings (unlike the seed-once boot path)."""
        return await self._apply_targets(parse_targets(text), reseed=reseed, source="import")

    async def _apply_targets(self, targets: dict[str, Target], *, reseed: bool, source: str) -> int:
        imported = 0
        for project_id, target in targets.items():
            if not reseed and await self.get_project(project_id) is not None:
                continue
            await self.set_project(
                project_id,
                enabled=target.enabled,
                webhook_secret=target.webhook_secret,
                source=source,
            )
            for repo in target.repos:
                await self.set_repo(
                    project_id,
                    repo.key,
                    github_repo=repo.github_repo,
                    base_branch=repo.base_branch,
                    source=source,
                )
            imported += 1
        return imported

    async def export_targets(self, workspace: str) -> str:
        """Serialize every project→repos mapping (incl. plaintext webhook secrets) to targets.yml
        content — the inverse of import_targets. Handle the output carefully."""
        async with self._sessionmaker() as session:
            projects = (await session.execute(select(ProjectMapping))).scalars().all()
            repos = (await session.execute(select(RepoMapping))).scalars().all()
        by_project: dict[str, list[RepoTarget]] = {}
        for r in repos:
            by_project.setdefault(r.plane_project_id, []).append(
                RepoTarget(key=r.key, github_repo=r.github_repo, base_branch=r.base_branch)
            )
        targets = {
            p.plane_project_id: Target(
                workspace=workspace,
                project_id=p.plane_project_id,
                enabled=p.enabled,
                webhook_secret=(self._box.decrypt(p.webhook_secret) if p.webhook_secret else None),
                repos=by_project.get(p.plane_project_id, []),
            )
            for p in projects
        }
        return dump_targets(targets)
