"""Persist private per-instrument observation preferences independently of groups."""

import sqlalchemy as sa
from alembic import op

revision = "0008_watchlist_observations"
down_revision = "0007_research_data_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "watchlist_observations",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column(
            "instrument_id", sa.String(36), sa.ForeignKey("instruments.id"), primary_key=True
        ),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="revision"),
        sa.CheckConstraint("length(notes) <= 2000", name="notes_length"),
    )


def downgrade():
    op.drop_table("watchlist_observations")
