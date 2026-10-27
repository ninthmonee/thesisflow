"""Record bounded-TTL worker liveness independently of research task leases."""

import sqlalchemy as sa
from alembic import op

revision = "0009_worker_heartbeats"
down_revision = "0008_watchlist_observations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(160), primary_key=True),
        sa.Column("instance_token", sa.String(36), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("current_run_id", sa.String(36), sa.ForeignKey("runs.id")),
        sa.CheckConstraint("state IN ('idle','busy','stopped')", name="state"),
    )
    op.create_index("ix_worker_heartbeats_expires_at", "worker_heartbeats", ["expires_at"])


def downgrade():
    op.drop_table("worker_heartbeats")
