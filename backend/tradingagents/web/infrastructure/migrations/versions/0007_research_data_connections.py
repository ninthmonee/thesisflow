"""Persist private research-account metadata and opaque credential references."""

import sqlalchemy as sa
from alembic import op

revision = "0007_research_data_connections"
down_revision = "0006_theme_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_data_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("credential_source", sa.String(40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("credential_ref", sa.String(240)),
        sa.Column("user_agent_ref", sa.String(240)),
        sa.Column("model", sa.String(120)),
        sa.Column("base_url", sa.String(240)),
        sa.Column("test_status", sa.String(16), nullable=False),
        sa.Column("tested_at", sa.DateTime()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "owner_id", "provider"),
        sa.CheckConstraint(
            "provider IN ('fred','alpha_vantage','typesafe_jev','sec_edgar')", name="provider"
        ),
        sa.CheckConstraint("credential_source IN ('keychain','legacy_environment')", name="source"),
        sa.CheckConstraint("revision >= 1", name="revision"),
    )


def downgrade():
    op.drop_table("research_data_connections")
