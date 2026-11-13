"""conversations.local_only: sticky conversation-level privacy

Revision ID: 0002
Revises: 0001

Backfill: any existing conversation that contains an assistant message whose stored route has
privacy == "local_only" is locked, so history written before this migration cannot leak to a cloud
provider on a later turn.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("local_only", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    bind = op.get_bind()
    messages = sa.table("messages", sa.column("conversation_id"), sa.column("route", sa.JSON))
    conversations = sa.table("conversations", sa.column("id"), sa.column("local_only"))
    locked = {
        row.conversation_id
        for row in bind.execute(sa.select(messages.c.conversation_id, messages.c.route))
        if isinstance(row.route, dict) and row.route.get("privacy") == "local_only"
    }
    for conversation_id in locked:
        bind.execute(
            conversations.update()
            .where(conversations.c.id == conversation_id)
            .values(local_only=True)
        )


def downgrade() -> None:
    op.drop_column("conversations", "local_only")
