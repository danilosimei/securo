"""check the values of transactions.category_source

097 added the column with no guard, unlike `categorization_events.source`:
a direct assignment (a rule preview, a model default) could store an
origin the pipeline does not know, and then neither protect nor revisit
the row. The CHECK takes the same list as the model
(`app.models.categorization.CATEGORY_SOURCES`).

Production holds only `none`, `provider` and `ai` at this point, so the
plain (validated) constraint is added: one scan of a small table under a
brief lock, no `NOT VALID` step needed.

Revision ID: 098
Revises: 097
"""
from alembic import op

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


def downgrade() -> None:
    op.drop_constraint(_NAME, "transactions", type_="check")
