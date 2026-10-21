"""store note on the merchant memory; conversation runs (`ask`) in the ledger

`merchant_memory.note` is what the person tells the AI about a store ("Aurino
e o jardineiro"), at most 200 characters; `note_updated_at` says when. Both
are nullable, additive columns: no table rewrite.

`categorization_runs.kind` gains `ask`: each message of the conversation about
a row is a run, so it counts toward the monthly cap. The check is dropped and
created again with the model's list (`app.models.categorization.RUN_KINDS`).
The downgrade deletes the `ask` runs first, as the old check could not hold
them.

Revision ID: 099
Revises: 098
"""
from alembic import op
import sqlalchemy as sa

revision = "099"
down_revision = "098"
branch_labels = None
depends_on = None

_NAME = "ck_categorization_run_kind"
_KINDS = ("classify", "organize", "redistribute", "ask")


def _in(kinds: tuple[str, ...]) -> str:
    return f"kind IN ({', '.join(repr(k) for k in kinds)})"


def upgrade() -> None:
    op.add_column("merchant_memory", sa.Column("note", sa.String(length=200), nullable=True))
    op.add_column(
        "merchant_memory", sa.Column("note_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint(_NAME, "categorization_runs", type_="check")
    op.create_check_constraint(_NAME, "categorization_runs", _in(_KINDS))


def downgrade() -> None:
    op.execute("DELETE FROM categorization_runs WHERE kind = 'ask'")
    op.drop_constraint(_NAME, "categorization_runs", type_="check")
    op.create_check_constraint(_NAME, "categorization_runs", _in(_KINDS[:-1]))
    op.drop_column("merchant_memory", "note_updated_at")
    op.drop_column("merchant_memory", "note")
