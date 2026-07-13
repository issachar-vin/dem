from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    STOPPED = "stopped"  # cancelled from the console; terminal, so it frees the dedupe key


class WorkflowState(StrEnum):
    """Canonical pipeline states, fixed in-app. StateMapping maps each onto whatever state
    already exists in a given Plane project (we map, we don't create — Plane CE has no state API
    for creation and boards are user-owned)."""

    BACKLOG = "backlog"
    READY_FOR_DEV = "ready_for_dev"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    # A ticket the pipeline parked for a human: the agent asked for input, made no changes, or
    # stalled. Mapped onto the operator's Plane "blocked" column so these surface on the board.
    BLOCKED = "blocked"
    READY_FOR_APPROVAL = "ready_for_approval"
    DONE = "done"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # At most one *active* (queued/running) job per (source, dedupe_key). This is the race-safe
        # backstop to enqueue_job's read-then-insert semantic dedupe: two concurrent Plane
        # re-deliveries of the same issue event can both pass the "any active job?" read, but only
        # one can win this insert — the other hits IntegrityError and is dropped. Partial (active
        # only) so a key is reusable once its job is terminal; NULL-guarded so github/poll jobs
        # (dedupe_key IS NULL) are exempt. SQLite-only backend (deviation: single-writer conductor).
        Index(
            "ix_jobs_active_dedupe",
            "source",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    # Provider delivery id (Plane/GitHub), used to dedupe redelivered webhooks.
    delivery_id: Mapped[str | None] = mapped_column(String(255), unique=True, default=None)
    # Semantic dedupe key (e.g. "<project_id>:<issue_id>"): at most one *active* job per key
    # (enforced by ix_jobs_active_dedupe above), so a re-fired Plane issue event doesn't stack a
    # second job. Distinct from delivery_id, which only catches a literal duplicate of one delivery.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), default=None)
    source: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Every raw provider payload that mapped to this job. The first delivery seeds it; each further
    # delivery deduped onto the same active job appends its payload here (named `raw_payloads`
    # because `metadata` is reserved on SQLAlchemy declarative models).
    raw_payloads: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    """Console operator. Single admin today (created on first spin-up), but the table already
    supports more rows so multi-user is a later add with no rewrite. Passwords are one-way
    hashed (argon2) — unlike the reversible Fernet Secret store."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    """A Plane project the conductor may work. A project owns many repos (see RepoMapping) and
    exactly one GitHub webhook secret, shared by all of them. `enabled` is the human opt-in gate:
    a project is only worked once someone turns it on. Seeded once from targets.yml; DB wins
    thereafter."""

    __tablename__ = "project_mappings"

    plane_project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    # Fernet ciphertext (keyed by DEM_SECRET_KEY); one secret per project, shared by its repos.
    webhook_secret: Mapped[str | None] = mapped_column(String, default=None)
    # Chosen display icon spec (`ms:<name>` or `fa:<class>`); None → Plane emoji or a default tile.
    icon: Mapped[str | None] = mapped_column(String(64), default=None)
    source: Mapped[str] = mapped_column(String(16), default="ui")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RepoMapping(Base):
    """One GitHub repo owned by a Plane project, identified by a short role key (`ui`, `backend`).
    A project maps to many of these; the planner assigns each ticket exactly one repo key."""

    __tablename__ = "repo_mappings"
    __table_args__ = (UniqueConstraint("plane_project_id", "key"),)

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    plane_project_id: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(64))
    github_repo: Mapped[str] = mapped_column(String(255))
    base_branch: Mapped[str] = mapped_column(String(255), default="main")
    # Chosen display icon spec (`ms:<name>` or `fa:<class>`); None → derive from the key.
    icon: Mapped[str | None] = mapped_column(String(64), default=None)
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
    # The repo key (from RepoMapping) the planner assigned this ticket; None → human-created ticket,
    # routed to the project's first repo. Drives clone/branch/PR/webhook routing.
    target_repo: Mapped[str | None] = mapped_column(String(64), default=None)
    # Ticket ids (Plane issue ids) that must reach `done` before this one may build. Set by the
    # planner from its plan's blocking graph; the scheduler gates on it.
    blocked_by: Mapped[list[str]] = mapped_column(JSON, default=list)
    pr_number: Mapped[int | None] = mapped_column(default=None)
    pr_url: Mapped[str | None] = mapped_column(String(512), default=None)
    engineer_session_id: Mapped[str | None] = mapped_column(String(128), default=None)
    loop_round: Mapped[int] = mapped_column(default=0)
    last_diff_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TicketPR(Base):
    """One PR a ticket opened, in one of its target repos. A ticket that spans repos opens several
    (one per repo with commits); the ticket is `done` only once every one of these has merged."""

    __tablename__ = "ticket_prs"
    __table_args__ = (UniqueConstraint("ticket_id", "repo_key"),)

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String(64), index=True)
    repo_key: Mapped[str] = mapped_column(String(64))
    github_repo: Mapped[str] = mapped_column(String(255))
    pr_number: Mapped[int] = mapped_column()
    pr_url: Mapped[str] = mapped_column(String(512), index=True)
    merged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRunStatus(StrEnum):
    RUNNING = "running"  # container live; `output` is being appended to as events stream in
    DONE = "done"
    FAILED = "failed"


class JobEvent(Base):
    """A conductor-side pipeline step (not agent stdout) — "preparing repo", "opened PR #8",
    "review passed", "failed: …" — recorded so the console can render a timeline of what the
    scheduler did for a job, interleaved with that job's agent runs by timestamp."""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(_AutoPK, index=True)
    ticket_id: Mapped[str] = mapped_column(String(64), default="")
    level: Mapped[str] = mapped_column(
        String(16), default="info"
    )  # info | success | warning | error
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRunLog(Base):
    """Captured output of one `claude -p` agent container run, so the console can show what an agent
    did (or why it failed) after the container is gone. One row per dispatch (engineer/reviewer/qa/
    planner and each resume round), keyed by ticket. The row is created when the container starts
    (`status=running`) and its `output` grows as stream-json events arrive, so the console can watch
    a run live; `status` moves to done/failed on exit."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    # The job whose dispatch produced this run. Runs are scoped and deleted by job (a re-triggered
    # ticket gets a new job, so its runs never collide with the old one's). Nullable for runs
    # captured before this column existed. ticket_id is retained for role/container grouping.
    job_id: Mapped[int | None] = mapped_column(_AutoPK, index=True, default=None)
    ticket_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    loop_round: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default=AgentRunStatus.RUNNING)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    # Raw container stdout (stream-json events) on success, or captured failure logs. Tail-capped in
    # the store so a runaway agent can't bloat the DB.
    output: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentPrompt(Base):
    """The editable prompt template for one agent role, stored in the DB so it can be changed from
    the console without a redeploy. `role` is the template key (engineer/planner/reviewer/qa and the
    engineer sub-prompts). `variant` names an agent "type": today only "default", but the column is
    the seam for future per-project/per-repo agent variants — the scheduler resolves a variant name
    to a row here. Seeded once from the bundled `prompts/*.md` files on first boot; DB wins after.
    """

    __tablename__ = "agent_prompts"
    __table_args__ = (UniqueConstraint("role", "variant"),)

    id: Mapped[int] = mapped_column(_AutoPK, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    variant: Mapped[str] = mapped_column(String(64), default="default")
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="seed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
