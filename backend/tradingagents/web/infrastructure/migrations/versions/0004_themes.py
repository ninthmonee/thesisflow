"""Add scoped versioned thematic observation baskets."""

import sqlalchemy as sa
from alembic import op

revision = "0004_themes"
down_revision = "0003_model_protocols"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "themes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("builtin_key", sa.String(80), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("workspace_id", "owner_id", "builtin_key"),
    )
    op.create_table(
        "theme_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("theme_id", sa.String(36), sa.ForeignKey("themes.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("effective_from", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("theme_id", "version"),
    )
    op.create_index("ix_theme_versions_theme_id", "theme_versions", ["theme_id"])
    op.create_table(
        "theme_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("theme_versions.id"), nullable=False),
        sa.Column("instrument_id", sa.String(36), sa.ForeignKey("instruments.id"), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.UniqueConstraint("version_id", "symbol"),
    )
    op.create_index("ix_theme_members_version_id", "theme_members", ["version_id"])


def downgrade():
    op.drop_table("theme_members")
    op.drop_table("theme_versions")
    op.drop_table("themes")
