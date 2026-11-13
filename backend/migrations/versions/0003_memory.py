"""memory: projects, memories (pgvector on PostgreSQL), memory_events

Revision ID: 0003
Revises: 0002

On PostgreSQL this needs the `vector` extension (CREATE EXTENSION requires the right to create
extensions; the pgvector/pgvector images and a superuser have it). The embedding column has no fixed
dimension. SQLite stores the vector as JSON text and searches it in numpy.
"""

import sqlalchemy as sa
from alembic import op

from app.db.models import EmbeddingType

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("privacy", sa.String(16), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column(
            "source_conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
        ),
        sa.Column("embedding", EmbeddingType()),
        sa.Column("embedding_model", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_memories_kind", "memories", ["kind"])
    op.create_index("ix_memories_status", "memories", ["status"])
    op.create_index("ix_memories_project_id", "memories", ["project_id"])
    op.create_index("ix_memories_expires_at", "memories", ["expires_at"])
    op.create_table(
        "memory_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("memory_id", sa.String(36)),
        sa.Column("actor", sa.String(24), nullable=False),
        sa.Column("detail", sa.JSON),
    )
    op.create_index("ix_memory_events_ts", "memory_events", ["ts"])


def downgrade() -> None:
    op.drop_table("memory_events")
    op.drop_table("memories")
    op.drop_table("projects")
