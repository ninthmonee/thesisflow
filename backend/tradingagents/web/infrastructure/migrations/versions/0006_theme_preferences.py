"""Persist scoped theme pin preferences without rewriting configuration history."""

import sqlalchemy as sa
from alembic import op

revision = "0006_theme_preferences"
down_revision = "0005_market_data_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "themes", sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("themes", sa.Column("pinned_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("themes", "pinned_at")
    op.drop_column("themes", "pinned")
