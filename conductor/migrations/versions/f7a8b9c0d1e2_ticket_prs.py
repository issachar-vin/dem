"""ticket_prs: a ticket can open many PRs (one per repo)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-11 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_prs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("repo_key", sa.String(length=64), nullable=False),
        sa.Column("github_repo", sa.String(length=255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("pr_url", sa.String(length=512), nullable=False),
        sa.Column("merged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("ticket_id", "repo_key"),
    )
    op.create_index("ix_ticket_prs_ticket_id", "ticket_prs", ["ticket_id"])
    op.create_index("ix_ticket_prs_pr_url", "ticket_prs", ["pr_url"])


def downgrade() -> None:
    op.drop_index("ix_ticket_prs_pr_url", table_name="ticket_prs")
    op.drop_index("ix_ticket_prs_ticket_id", table_name="ticket_prs")
    op.drop_table("ticket_prs")
