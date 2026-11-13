"""preferences: a few owner-level on/off switches

Revision ID: 0005
Revises: 0004

Additive only: one new table.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preferences",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("value", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("preferences")
