"""category description

`categories.description` is what belongs in the category, in the person's
words (up to 300 characters). The categorizer reads it as the category's rule.
Nullable: existing categories stay without one and work as before.

Revision ID: 101
Revises: 100
"""
from alembic import op
import sqlalchemy as sa

revision = "101"
down_revision = "100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("categories", sa.Column("description", sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column("categories", "description")
