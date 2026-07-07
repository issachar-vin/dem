from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from conductor.db import Base

# A BigInteger primary key is not a rowid alias on SQLite, so it won't autoincrement there
# (and SQLite is the default backend). Fall back to INTEGER on SQLite, keep BIGINT on Postgres.
_AutoPK = BigInteger().with_variant(Integer, "sqlite")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class WorkflowState(StrEnum):
    """Canonical pipeline states, fixed in-app. StateMapping maps each onto whatever state
    already exists in a given Plane project (we map, we don't create — Plane CE has no state API
    for creation and boards are user-owned)."""

    BACKLOG = "backlog"
    READY_FOR_DEV = "ready_for_dev"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    READY_FOR_APPROVAL = "ready_for_approval"
    DONE = "done"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    # Provider delivery id (Plane/GitHub), used to dedupe redelivered webhooks.
    delivery_id: Mapped[str | None] = mapped_column(String(255), unique=True, default=None)
    source: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Secret(Base):
    """A credential stored encrypted at rest (Fernet, keyed by DEM_SECRET_KEY)."""

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(String)
    last_four: Mapped[str] = mapped_column(String(4), default="")
    source: Mapped[str] = mapped_column(String(16), default="ui")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Setting(Base):
    """Non-secret application config; DB is the source of truth after first-boot seeding."""

    __tablename__ = "settings"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String(16), default="ui")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProjectMapping(Base):
    """Routes a Plane project to the GitHub repo the conductor builds it into (one repo per
    project). Seeded once from targets.yml; the DB wins thereafter."""

    __tablename__ = "project_mappings"

    plane_project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo: Mapped[str] = mapped_column(String(255))
    base_branch: Mapped[str] = mapped_column(String(255), default="main")
    source: Mapped[str] = mapped_column(String(16), default="ui")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StateMapping(Base):
    """Maps a canonical WorkflowState onto a concrete Plane state id, per project."""

    __tablename__ = "state_mappings"
    __table_args__ = (UniqueConstraint("plane_project_id", "workflow_state"),)

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    plane_project_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_state: Mapped[str] = mapped_column(String(32))
    plane_state_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Ticket(Base):
    """Conductor-side mirror of a Plane work item's pipeline state.

    Ticket-visible fields are also written to Plane custom properties; this table
    is the durable local copy the state machine drives.
    """

    __tablename__ = "tickets"

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_status: Mapped[str] = mapped_column(String(32), default="pending")
    pr_number: Mapped[int | None] = mapped_column(default=None)
    pr_url: Mapped[str | None] = mapped_column(String(512), default=None)
    engineer_session_id: Mapped[str | None] = mapped_column(String(128), default=None)
    loop_round: Mapped[int] = mapped_column(default=0)
    last_diff_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
