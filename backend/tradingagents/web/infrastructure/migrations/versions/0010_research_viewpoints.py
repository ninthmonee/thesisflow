"""Manual research follow-up, scoped to the confirming owner."""

import sqlalchemy as sa
from alembic import op

revision = "0010_research_viewpoints"
down_revision = "0009_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("research_viewpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("ticker", sa.String(64), nullable=False),
        sa.Column("stance", sa.String(20), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=False),
        sa.Column("invalidation", sa.Text(), nullable=False),
        sa.Column("review_on", sa.String(10)),
        sa.Column("review_notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "owner_id", "run_id"),
        sa.CheckConstraint("revision >= 1", name="revision"),
        sa.CheckConstraint("status IN ('active','invalidated','archived')", name="status"),
    )


def downgrade():
    op.drop_table("research_viewpoints")
