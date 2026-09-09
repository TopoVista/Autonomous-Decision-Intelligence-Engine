"""initial schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-04-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("clerk_id", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_users_clerk_id", "users", ["clerk_id"])

    op.create_table(
        "db_connections",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("db_type", sa.String(50), nullable=False),
        sa.Column("host", sa.String(500), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="5432"),
        sa.Column("database_name", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("ssl_mode", sa.String(50), server_default="prefer"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true()),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_connections_user_id", "db_connections", ["user_id"])

    op.create_table(
        "query_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("db_connections.id"), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_sessions_user_id", "query_sessions", ["user_id"])
    op.create_index("idx_sessions_created_at", "query_sessions", ["created_at"])

    op.create_table(
        "query_history",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("query_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_question", sa.Text(), nullable=False),
        sa.Column("intent_type", sa.String(100)),
        sa.Column("task_plan", sa.JSON()),
        sa.Column("generated_queries", sa.JSON()),
        sa.Column("analysis_result", sa.JSON()),
        sa.Column("hypotheses", sa.JSON()),
        sa.Column("final_insight", sa.Text()),
        sa.Column("anomalies_detected", sa.JSON()),
        sa.Column("execution_time_ms", sa.Integer()),
        sa.Column("total_tokens_used", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_history_session_id", "query_history", ["session_id"])
    op.create_index("idx_history_user_id", "query_history", ["user_id"])
    op.create_index("idx_history_created_at", "query_history", ["created_at"])

    op.create_table(
        "schema_cache",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("db_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("table_count", sa.Integer()),
        sa.Column("cached_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_schema_cache_connection", "schema_cache", ["connection_id"], unique=True)

    op.create_table(
        "query_embeddings",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("db_connections.id"), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("insight_text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_embeddings_user", "query_embeddings", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_embeddings_user", table_name="query_embeddings")
    op.drop_table("query_embeddings")
    op.drop_index("idx_schema_cache_connection", table_name="schema_cache")
    op.drop_table("schema_cache")
    op.drop_index("idx_history_created_at", table_name="query_history")
    op.drop_index("idx_history_user_id", table_name="query_history")
    op.drop_index("idx_history_session_id", table_name="query_history")
    op.drop_table("query_history")
    op.drop_index("idx_sessions_created_at", table_name="query_sessions")
    op.drop_index("idx_sessions_user_id", table_name="query_sessions")
    op.drop_table("query_sessions")
    op.drop_index("idx_connections_user_id", table_name="db_connections")
    op.drop_table("db_connections")
    op.drop_index("idx_users_clerk_id", table_name="users")
    op.drop_table("users")
