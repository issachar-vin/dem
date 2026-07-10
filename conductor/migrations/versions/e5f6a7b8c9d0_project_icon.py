"""project_mappings icon

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-10 02:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: a project with no chosen icon falls back to its Plane emoji or a default tile.
    op.add_column("project_mappings", sa.Column("icon", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("project_mappings", "icon")
