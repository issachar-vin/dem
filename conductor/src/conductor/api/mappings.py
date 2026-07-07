from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from conductor import plane
from conductor.mappings import MappingStore
from conductor.models import WorkflowState
from conductor.plane import PlaneError
from conductor.store import ConfigStore

router = APIRouter(prefix="/api/mappings", tags=["mappings"])


class ProjectBody(BaseModel):
    repo: str
    base_branch: str = "main"


class StateBody(BaseModel):
    workflow_state: WorkflowState
    plane_state_id: str


def _mappings(request: Request) -> MappingStore:
    store: MappingStore = request.app.state.mappings
    return store


@router.get("/workflow-states")
async def workflow_states() -> list[str]:
    return [s.value for s in WorkflowState]


@router.get("/projects")
async def list_projects(request: Request) -> list[dict[str, Any]]:
    return await _mappings(request).list_projects()


@router.put("/projects/{project_id}")
async def set_project(project_id: str, body: ProjectBody, request: Request) -> dict[str, str]:
    if body.repo.count("/") != 1 or body.repo.startswith("/") or body.repo.endswith("/"):
        raise HTTPException(status_code=422, detail="repo must be in 'owner/name' form.")
    await _mappings(request).set_project(project_id, repo=body.repo, base_branch=body.base_branch)
    return {"plane_project_id": project_id, "status": "set"}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request) -> dict[str, str]:
    if not await _mappings(request).delete_project(project_id):
        raise HTTPException(status_code=404, detail=f"No mapping for project {project_id}.")
    return {"plane_project_id": project_id, "status": "deleted"}


@router.get("/projects/{project_id}/states")
async def list_state_mappings(project_id: str, request: Request) -> list[dict[str, str]]:
    return await _mappings(request).list_states(project_id)


@router.put("/projects/{project_id}/states")
async def set_state_mapping(project_id: str, body: StateBody, request: Request) -> dict[str, str]:
    await _mappings(request).set_state(project_id, body.workflow_state, body.plane_state_id)
    return {"workflow_state": body.workflow_state.value, "status": "set"}


@router.get("/projects/{project_id}/state-scan")
async def scan_states(project_id: str, request: Request) -> list[dict[str, Any]]:
    """Live scan of the project's actual Plane states, for the mapping UI to pick from."""
    store: ConfigStore = request.app.state.store
    client = plane.client_from_resolved(await store.resolved())
    try:
        states = await client.list_states(project_id)
    except PlaneError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    return [{"id": s.get("id"), "name": s.get("name"), "group": s.get("group")} for s in states]
