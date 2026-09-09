"""add datasets table

Revision ID: 002_add_datasets
Revises: 001_initial_schema
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002_add_datasets"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Releases before Alembic migrations used Base.metadata.create_all(), so
    # some live databases already have this exact table while their migration
    # version remains at 001. Detect it rather than failing a deployment or
    # recreating user data.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("datasets"):
        op.create_table(
            "datasets",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("filename", sa.String(length=512), nullable=False),
            sa.Column("source_type", sa.String(length=32), nullable=False, server_default="file"),
            sa.Column("file_path", sa.String(length=1024), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("column_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("descriptor_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("idx_datasets_user_id", "datasets", ["user_id"])
        return

    index_names = {index["name"] for index in inspector.get_indexes("datasets")}
    if "idx_datasets_user_id" not in index_names:
        op.create_index("idx_datasets_user_id", "datasets", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_datasets_user_id", table_name="datasets")
    op.drop_table("datasets")
