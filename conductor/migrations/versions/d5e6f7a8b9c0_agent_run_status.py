"""agent_runs live streaming: job_id + status

Revision ID: d5e6f7a8b9c0
Revises: b7c8d9e0f1a2
Create Date: 2026-07-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows were captured only at completion, so they are terminal → 'done'. New rows
    # default to 'running' in the app (start_run) and are finished on container exit.
    op.add_column(
        "agent_runs",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="done"),
    )
    # The job that produced the run; pre-existing rows predate the link → NULL.
    op.add_column("agent_runs", sa.Column("job_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_agent_runs_job_id", "agent_runs", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_job_id", table_name="agent_runs")
    op.drop_column("agent_runs", "job_id")
    op.drop_column("agent_runs", "status")
