"""Separate model provider labels, wire protocols and secret custom headers."""

import sqlalchemy as sa
from alembic import op

revision = "0003_model_protocols"
down_revision = "0002_data_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("model_connections", sa.Column("vendor_name", sa.String(120), nullable=True))
    op.add_column(
        "model_connections",
        sa.Column("api_protocol", sa.String(40), nullable=False, server_default="auto"),
    )
    op.add_column("model_connections", sa.Column("extra_header_ref", sa.String(240), nullable=True))
    op.add_column(
        "model_connections",
        sa.Column("header_names", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade():
    with op.batch_alter_table("model_connections") as batch:
        batch.drop_column("header_names")
        batch.drop_column("extra_header_ref")
        batch.drop_column("api_protocol")
        batch.drop_column("vendor_name")
