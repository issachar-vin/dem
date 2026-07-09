import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import plane
from conductor.mappings import MappingStore
from conductor.models import Job
from conductor.store import ConfigStore

logger = logging.getLogger("conductor")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class PlaneIssueData(BaseModel):
    """The `data` block of a Plane issue webhook. IDs and labels arrive as UUID strings; every
    field is optional because Plane omits them on some actions (see docs/HANDOFF.md, Phase 2b)."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    project: str = ""
    labels: list[str] = Field(default_factory=list)
    parent: str | None = None


class PlaneWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str = ""
    action: str = ""
    data: PlaneIssueData = Field(default_factory=PlaneIssueData)


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """HMAC-SHA256 over the raw body, hex digest, timing-safe. Plane signs `json.dumps(payload)`
    which is byte-identical to the delivered body (see docs/HANDOFF.md, Phase 2b research)."""
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/plane")
async def plane_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    store: ConfigStore = request.app.state.store
    resolved = await store.resolved()

    if not verify_signature(
        body,
        request.headers.get("X-Plane-Signature"),
        resolved.get("plane_webhook_secret", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature.")

    try:
        payload = PlaneWebhook.model_validate(await request.json())
    except ValidationError:
        raise HTTPException(status_code=400, detail="Malformed webhook payload.") from None

    event = request.headers.get("X-Plane-Event") or payload.event
    if event != "issue":
        return {"status": "ignored", "reason": f"event {event!r} not handled"}

    data = payload.data
    project_id = data.project
    issue_id = data.id

    mappings: MappingStore = request.app.state.mappings
    mapping = await mappings.get_project(project_id)
    if mapping is None or not mapping.enabled:
        return {"status": "ignored", "reason": f"project {project_id} is not enabled"}

    if not await _is_epic(resolved, data):
        return {"status": "ignored", "reason": "issue is not an epic"}

    delivery_id = request.headers.get("X-Plane-Delivery")
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    # The epic is project-scoped; the planner assigns each ticket its target repo (Phase 5),
    # so no single repo is pinned here.
    created = await _create_job(
        sessionmaker,
        delivery_id=delivery_id,
        event_type=f"issue.{payload.action}",
        payload={"project_id": project_id, "issue_id": issue_id},
    )
    if not created:
        return {"status": "duplicate", "delivery_id": delivery_id or ""}
    logger.info("Queued job for epic issue %s (project %s)", issue_id, project_id)
    return {"status": "queued", "issue_id": issue_id}


async def _is_epic(resolved: dict[str, str], data: PlaneIssueData) -> bool:
    signal = resolved.get("plane_epic_signal", "label")
    if signal == "parentless":
        return data.parent in (None, "")
    # "label" (and "type", which Plane Community lacks — fall back to the epic label).
    label_ids = set(data.labels)
    if not label_ids:
        return False
    client = plane.client_from_resolved(resolved)
    labels = await client.list_labels(data.project)
    epic_ids = {str(x["id"]) for x in labels if str(x.get("name", "")).lower() == "epic"}
    return bool(epic_ids & label_ids)


async def _create_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    delivery_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    """Insert a deduped Job. Returns False if the delivery id was already seen."""
    async with sessionmaker() as session:
        session.add(
            Job(
                delivery_id=delivery_id,
                source="plane",
                event_type=event_type,
                payload=payload,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
    return True
