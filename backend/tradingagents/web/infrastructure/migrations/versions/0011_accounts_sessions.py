"""Independent accounts, opaque sessions and guarded first-owner adoption."""

import sqlalchemy as sa
from alembic import op

revision = "0011_accounts_sessions"
down_revision = "0010_research_viewpoints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("auth_accounts",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False))
    op.create_table("auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("auth_accounts.user_id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table("auth_settings",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("registration_mode", sa.String(16), nullable=False),
        sa.Column("setup_token_digest", sa.String(64)),
        sa.Column("setup_created_at", sa.DateTime()),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.CheckConstraint("registration_mode IN ('open','closed')", name="registration_mode"))
    op.create_table("auth_rate_limits",
        sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False))
    op.create_index("ix_auth_rate_limits_expires_at", "auth_rate_limits", ["expires_at"])


def downgrade():
    op.drop_table("auth_rate_limits")
    op.drop_table("auth_settings")
    op.drop_table("auth_sessions")
    op.drop_table("auth_accounts")
