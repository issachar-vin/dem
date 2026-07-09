"""job dedupe_key

Revision ID: a1b2c3d4e5f6
Revises: 8c03955898af
Create Date: 2026-07-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "8c03955898af"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("dedupe_key", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_jobs_dedupe_key"), "jobs", ["dedupe_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_dedupe_key"), table_name="jobs")
    op.drop_column("jobs", "dedupe_key")
