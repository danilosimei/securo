"""check the values of transactions.category_source; note the memory picks events moved

097 added the column with no guard, unlike `categorization_events.source`:
a direct assignment (a rule preview, a model default) could store an
origin the pipeline does not know, and then neither protect nor revisit
the row. The CHECK takes the same list as the model
(`app.models.categorization.CATEGORY_SOURCES`).

Production holds only `none`, `provider` and `ai` at this point, so the
plain (validated) constraint is added: one scan of a small table under a
brief lock, no `NOT VALID` step needed.

`categorization_events.memory_pick` marks the events the merchant memory
counted one pick for: a person's choice taught it, a person's clear took
it back. Undoing the event reverses exactly that pick, however the undo is
grouped. Additive, with a constant default: no table rewrite on Postgres.
Events written before it read false, so undoing them leaves the memory as
it is.

Revision ID: 098
Revises: 097
"""
from alembic import op
import sqlalchemy as sa

revision = "098"
down_revision = "097"
branch_labels = None
depends_on = None

_NAME = "ck_transaction_category_source"
_SOURCES = (
    "none", "user", "rule", "memory", "ai", "provider",
    "installment", "import", "recurring", "legacy",
)


def upgrade() -> None:
    op.create_check_constraint(
        _NAME,
        "transactions",
        "category_source IN (" + ", ".join(f"'{s}'" for s in _SOURCES) + ")",
    )
    op.add_column(
        "categorization_events",
        sa.Column("memory_pick", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("categorization_events", "memory_pick")
    op.drop_constraint(_NAME, "transactions", type_="check")
