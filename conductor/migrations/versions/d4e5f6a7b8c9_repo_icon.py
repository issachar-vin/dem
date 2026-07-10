"""repo_mappings icon

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-10 02:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing repos keep a key-derived icon until one is explicitly picked.
    op.add_column("repo_mappings", sa.Column("icon", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("repo_mappings", "icon")
