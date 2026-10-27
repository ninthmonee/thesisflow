"""Private, versioned market-data connection references."""

import sqlalchemy as sa
from alembic import op

revision = "0005_market_data_connections"
down_revision = "0004_themes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_data_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("feed", sa.String(40), nullable=False),
        sa.Column("credential_ref", sa.String(240), nullable=False),
        sa.Column("secondary_credential_ref", sa.String(240), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("test_results", sa.JSON(), nullable=False),
        sa.Column("tested_at", sa.DateTime(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "provider IN ('massive', 'intrinio', 'alpaca')",
            name="ck_market_data_connections_provider",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_market_data_connections_revision"),
    )


def downgrade():
    op.drop_table("market_data_connections")
