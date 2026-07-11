"""tickets target_repo + blocked_by

Revision ID: a6b7c8d9e0f1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: human-created tickets have no planner-assigned repo (routed to the first repo).
    op.add_column("tickets", sa.Column("target_repo", sa.String(length=64), nullable=True))
    # JSON list of blocking ticket ids; server default '[]' so existing rows read as unblocked.
    op.add_column(
        "tickets",
        sa.Column("blocked_by", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("tickets", "blocked_by")
    op.drop_column("tickets", "target_repo")
