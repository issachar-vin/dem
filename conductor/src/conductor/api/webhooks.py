import hashlib
import hmac
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conductor import plane
from conductor.jobs import enqueue_job
from conductor.mappings import MappingStore
from conductor.models import WorkflowState
from conductor.store import ConfigStore

logger = logging.getLogger("conductor")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ── Plane ────────────────────────────────────────────────────────────────────
class PlaneIssueData(BaseModel):
    """The `data` block of a Plane issue webhook. IDs and labels arrive as UUID strings; every
    field is optional because Plane omits them on some actions (see docs/HANDOFF.md, Phase 2b)."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    project: str = ""
    labels: list[str] = Field(default_factory=list)
    parent: str | None = None
    state: str = ""


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
    mappings: MappingStore = request.app.state.mappings
    mapping = await mappings.get_project(data.project)
    if mapping is None or not mapping.enabled:
        return {"status": "ignored", "reason": f"project {data.project} is not enabled"}

    trigger = await _plane_trigger(resolved, mappings, data)
    if trigger is None:
        return {"status": "ignored", "reason": "issue is not an epic or ready for dev"}

    # The epic/ticket is project-scoped; the planner assigns each ticket its target repo (Phase 5),
    # so no single repo is pinned here. Semantic dedupe keeps a re-fired issue event from stacking a
    # second job while one for the same issue is still in flight (docs/HANDOFF.md, Phase 2b #3).
    job = await enqueue_job(
        request.app.state.sessionmaker,
        source="plane",
        event_type=f"issue.{payload.action}",
        payload={"project_id": data.project, "issue_id": data.id, "trigger": trigger},
        delivery_id=request.headers.get("X-Plane-Delivery"),
        dedupe_key=f"{data.project}:{data.id}",
    )
    if job is None:
        return {"status": "duplicate", "delivery_id": request.headers.get("X-Plane-Delivery") or ""}
    logger.info("Queued %s job for issue %s (project %s)", trigger, data.id, data.project)
    return {"status": "queued", "issue_id": data.id}


async def _plane_trigger(
    resolved: dict[str, str], mappings: MappingStore, data: PlaneIssueData
) -> str | None:
    """Which pipeline entry point (if any) this issue event fires — the two intake triggers from
    the design: an `epic`-labelled issue → the planner; an issue in `ready_for_dev` → the engineer.
    Anything else is project noise and ignored."""
    if await _is_epic(resolved, data):
        return "planner"
    if data.state:
        ready = await mappings.get_state_id(data.project, WorkflowState.READY_FOR_DEV)
        if ready is not None and data.state == ready:
            return "engineer"
    return None


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


# ── GitHub ───────────────────────────────────────────────────────────────────
class GitHubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str = ""


class GitHubPullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int | None = None
    state: str = ""
    merged: bool = False


class GitHubReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    state: str = ""


class GitHubThread(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolved: bool = False


class GitHubWebhook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = ""
    repository: GitHubRepository = Field(default_factory=GitHubRepository)
    pull_request: GitHubPullRequest | None = None
    review: GitHubReview | None = None
    thread: GitHubThread | None = None


def _pr_number(webhook: GitHubWebhook) -> int | None:
    return webhook.pull_request.number if webhook.pull_request else None


# event name → payload builder. A new subscribed event is one entry here (open/closed); the
# webhook body stays a dumb enqueue — the state machine (Phase 4+) decides what each job does.
GITHUB_EVENT_PAYLOADS: dict[str, Callable[[GitHubWebhook], dict[str, Any]]] = {
    "pull_request": lambda w: {
        "pr_number": _pr_number(w),
        "merged": bool(w.pull_request and w.pull_request.merged),
    },
    "pull_request_review": lambda w: {
        "pr_number": _pr_number(w),
        "review_state": w.review.state if w.review else "",
    },
    "pull_request_review_comment": lambda w: {"pr_number": _pr_number(w)},
    "pull_request_review_thread": lambda w: {
        "pr_number": _pr_number(w),
        "resolved": bool(w.thread and w.thread.resolved),
    },
}


def _github_signature_ok(body: bytes, header: str | None, secret: str) -> bool:
    if not header or not header.startswith("sha256=") or not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@router.post("/github")
async def github_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    try:
        payload = GitHubWebhook.model_validate(await request.json())
    except ValidationError:
        raise HTTPException(status_code=400, detail="Malformed webhook payload.") from None

    # Verify-after-lookup: the repo name in an unverified body is trusted only far enough to find
    # whose secret to check the signature against (the multi-tenant-webhook pattern). Secrets are
    # per-project (CLAUDE.md deviation #7), so there is no global secret to fall back on.
    mappings: MappingStore = request.app.state.mappings
    project_id = await mappings.get_project_for_repo(payload.repository.full_name)
    secret = await mappings.get_webhook_secret(project_id) if project_id else None
    if not _github_signature_ok(body, request.headers.get("X-Hub-Signature-256"), secret or ""):
        raise HTTPException(status_code=401, detail="Invalid or missing webhook signature.")

    event = request.headers.get("X-GitHub-Event", "")
    build = GITHUB_EVENT_PAYLOADS.get(event)
    if build is None:
        return {"status": "ignored", "reason": f"event {event!r} not handled"}

    job = await enqueue_job(
        request.app.state.sessionmaker,
        source="github",
        event_type=f"{event}.{payload.action}",
        payload={
            "project_id": project_id,
            "repo": payload.repository.full_name,
            "action": payload.action,
            **build(payload),
        },
        delivery_id=request.headers.get("X-GitHub-Delivery"),
    )
    if job is None:
        return {
            "status": "duplicate",
            "delivery_id": request.headers.get("X-GitHub-Delivery") or "",
        }
    logger.info(
        "Queued github %s.%s for %s (project %s)",
        event,
        payload.action,
        payload.repository.full_name,
        project_id,
    )
    return {"status": "queued", "event": event}
