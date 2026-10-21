"""category report kind; store key written on the transaction

`categories.report_kind` says how reports read a category: `normal` (spending
or whatever the sign says), `income` (counts as earned money) or `refund`
(reduces spending). Categories in the seeded "income" group, in any language,
become `income` here; the rest stay `normal`.

`transactions.merchant_key` is the store key the categorization already
computes (`app.services.categorization.merchant_key`, truncated as the memory
stores it), written on the row so reports can group by store with an index.
Nullable and filled by the model hook on every write; existing rows are filled
by `scripts/backfill_merchant_keys.py`, not here (the rule is code, not SQL).

Revision ID: 100
Revises: 099
"""
import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "100"
down_revision = "099"
branch_labels = None
depends_on = None

_CHECK = "ck_categories_report_kind"
_KINDS = ("normal", "income", "refund")
_INDEX = "ix_transactions_ws_merchant"
_TAXONOMY = Path(__file__).resolve().parents[2] / "app" / "data" / "default_categories.json"


def _income_group_names() -> list[str]:
    taxonomy = json.loads(_TAXONOMY.read_text(encoding="utf-8"))
    group = taxonomy["groups"]["income"]
    return sorted(set(group["names"].values()) | set(group.get("aliases", ())))


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("report_kind", sa.String(10), nullable=False, server_default="normal"),
    )
    kinds = ", ".join(repr(k) for k in _KINDS)
    op.create_check_constraint(_CHECK, "categories", f"report_kind IN ({kinds})")

    groups = sa.table("category_groups", sa.column("id"), sa.column("name"))
    categories = sa.table("categories", sa.column("group_id"), sa.column("report_kind"))
    op.execute(
        categories.update()
        .where(categories.c.group_id.in_(sa.select(groups.c.id).where(groups.c.name.in_(_income_group_names()))))
        .values(report_kind="income")
    )

    op.add_column("transactions", sa.Column("merchant_key", sa.String(255), nullable=True))
    op.create_index(_INDEX, "transactions", ["workspace_id", "merchant_key"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="transactions")
    op.drop_column("transactions", "merchant_key")
    op.drop_constraint(_CHECK, "categories", type_="check")
    op.drop_column("categories", "report_kind")
