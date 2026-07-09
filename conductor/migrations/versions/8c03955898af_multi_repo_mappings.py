"""multi-repo mappings

Revision ID: 8c03955898af
Revises: 9ff29322a03f
Create Date: 2026-07-09 00:24:46.205383
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c03955898af"
down_revision: str | None = "9ff29322a03f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Clean cut to one project → many repos: repo/base_branch leave project_mappings for the new
    # repo_mappings table; project_mappings gains the opt-in flag + a project-scoped webhook secret.
    op.create_table(
        "repo_mappings",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("plane_project_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("github_repo", sa.String(length=255), nullable=False),
        sa.Column("base_branch", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plane_project_id", "key"),
    )
    op.create_index(
        op.f("ix_repo_mappings_plane_project_id"),
        "repo_mappings",
        ["plane_project_id"],
        unique=False,
    )
    # server_default so the NOT NULL add succeeds against any existing rows.
    op.add_column(
        "project_mappings",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("project_mappings", sa.Column("webhook_secret", sa.String(), nullable=True))
    op.drop_column("project_mappings", "repo")
    op.drop_column("project_mappings", "base_branch")


def downgrade() -> None:
    # server_defaults so the NOT NULL re-adds run against existing rows; the original repo values
    # are not recoverable after the clean cut, so callers must re-populate them.
    op.add_column(
        "project_mappings",
        sa.Column("base_branch", sa.VARCHAR(length=255), nullable=False, server_default="main"),
    )
    op.add_column(
        "project_mappings",
        sa.Column("repo", sa.VARCHAR(length=255), nullable=False, server_default=""),
    )
    op.drop_column("project_mappings", "webhook_secret")
    op.drop_column("project_mappings", "enabled")
    op.drop_index(op.f("ix_repo_mappings_plane_project_id"), table_name="repo_mappings")
    op.drop_table("repo_mappings")
