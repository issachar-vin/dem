"""agent_prompts: editable per-role prompt templates, seeded from the bundled defaults on boot

Revision ID: c8d9e0f1a2b3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-12 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_prompts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="seed"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("role", "variant"),
    )
    op.create_index("ix_agent_prompts_role", "agent_prompts", ["role"])


def downgrade() -> None:
    op.drop_index("ix_agent_prompts_role", table_name="agent_prompts")
    op.drop_table("agent_prompts")
