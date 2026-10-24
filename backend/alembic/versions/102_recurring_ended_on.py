"""recurring ended on

`merchant_memory.recurring_ended_on` is the day the person said a fixed cost
of the store is over ("Já acabou" in Reports > Fixos). Until a new charge
comes after that day, the store shows as stopped without waiting for a whole
month without charges. Nullable: no store is marked as ended.

Revision ID: 102
Revises: 101
"""
from alembic import op
import sqlalchemy as sa

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("merchant_memory", sa.Column("recurring_ended_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("merchant_memory", "recurring_ended_on")
