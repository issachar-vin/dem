"""job active dedupe unique index

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-09 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing duplicate active jobs (from before the backstop) would violate the unique index,
    # so collapse each (source, dedupe_key) group of active jobs to its earliest row first. Dropping
    # a redundant duplicate is safe: nothing consumes jobs yet, and the kept row is the one to work.
    op.execute(
        sa.text(
            "DELETE FROM jobs WHERE dedupe_key IS NOT NULL "
            "AND status IN ('queued', 'running') AND id NOT IN ("
            "  SELECT MIN(id) FROM jobs "
            "  WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running') "
            "  GROUP BY source, dedupe_key"
            ")"
        )
    )
    op.drop_index(op.f("ix_jobs_dedupe_key"), table_name="jobs")
    op.create_index(
        "ix_jobs_active_dedupe",
        "jobs",
        ["source", "dedupe_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_active_dedupe", table_name="jobs")
    op.create_index(op.f("ix_jobs_dedupe_key"), "jobs", ["dedupe_key"], unique=False)
