from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import ProjectMapping, StateMapping, WorkflowState
from conductor.targets import load_targets


class MappingStore:
    """DB-backed project↔repo and canonical-state↔Plane-state mappings."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    # ── project mappings ─────────────────────────────────────────────────────
    async def get_project(self, project_id: str) -> ProjectMapping | None:
        async with self._sessionmaker() as session:
            return await session.get(ProjectMapping, project_id)

    async def list_projects(self) -> list[dict[str, Any]]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(ProjectMapping))).scalars()
            return [_project_dict(row) for row in rows]

    async def set_project(
        self, project_id: str, *, repo: str, base_branch: str = "main", source: str = "ui"
    ) -> None:
        async with self._sessionmaker() as session:
            row = await session.get(ProjectMapping, project_id)
            if row is None:
                session.add(
                    ProjectMapping(
                        plane_project_id=project_id,
                        repo=repo,
                        base_branch=base_branch,
                        source=source,
                    )
                )
            else:
                row.repo = repo
                row.base_branch = base_branch
                row.source = source
            await session.commit()

    async def delete_project(self, project_id: str) -> bool:
        async with self._sessionmaker() as session:
            row = await session.get(ProjectMapping, project_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    # ── state mappings ───────────────────────────────────────────────────────
    async def list_states(self, project_id: str) -> list[dict[str, str]]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(StateMapping).where(StateMapping.plane_project_id == project_id)
                )
            ).scalars()
            return [
                {"workflow_state": r.workflow_state, "plane_state_id": r.plane_state_id}
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

    # ── seed import (targets.yml → project mappings, seed-once) ───────────────
    async def import_targets(self, path: Path, *, reseed: bool = False) -> int:
        imported = 0
        for project_id, target in load_targets(path).items():
            if not reseed and await self.get_project(project_id) is not None:
                continue
            await self.set_project(
                project_id,
                repo=target.github_repo,
                base_branch=target.base_branch,
                source="seed",
            )
            imported += 1
        return imported


def _project_dict(row: ProjectMapping) -> dict[str, Any]:
    return {
        "plane_project_id": row.plane_project_id,
        "repo": row.repo,
        "base_branch": row.base_branch,
        "source": row.source,
    }
