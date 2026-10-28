"""Owned, isolated local Agent runtime connections."""

import sqlalchemy as sa
from alembic import op

revision = "0012_agent_runtimes"
down_revision = "0011_accounts_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_runtime_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("runtime_type", sa.String(32), nullable=False),
        sa.Column("executable_path", sa.String(1000)),
        sa.Column("model_id", sa.String(240), nullable=False),
        sa.Column("reasoning_effort", sa.String(24)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auth_mode", sa.String(32), nullable=False),
        sa.Column("model_profile_id", sa.String(36), sa.ForeignKey("model_profiles.id", ondelete="SET NULL")),
        sa.Column("readiness", sa.String(24), nullable=False),
        sa.Column("probe_result", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("runtime_type = 'codex'", name="runtime_type"),
        sa.CheckConstraint("auth_mode IN ('runtime_login','model_connection')", name="auth_mode"),
        sa.CheckConstraint("revision >= 1", name="revision"))
    op.create_index("ix_agent_runtime_connections_workspace_id", "agent_runtime_connections", ["workspace_id"])
    op.create_index("ix_agent_runtime_connections_owner_id", "agent_runtime_connections", ["owner_id"])


def downgrade():
    op.drop_table("agent_runtime_connections")
