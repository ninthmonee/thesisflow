"""Persist workspace market-runtime settings without rewriting revision one."""

import sqlalchemy as sa
from alembic import op

revision = "0002_data_settings"
down_revision = "0001_web_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_settings",
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_data_settings_revision")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_data_settings_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_data_settings")),
    )


def downgrade():
    op.drop_table("data_settings")
