"""store note on the merchant memory; conversation runs (`ask`) in the ledger

`merchant_memory.note` is what the person tells the AI about a store ("Aurino
e o jardineiro"), at most 200 characters; `note_updated_at` says when. Both
are nullable, additive columns: no table rewrite.

`categorization_runs.kind` gains `ask`: each message of the conversation about
a row is a run, so it counts toward the monthly cap. The check is dropped and
created again with the model's list (`app.models.categorization.RUN_KINDS`).
The downgrade deletes the `ask` runs first, as the old check could not hold
them.

Category groups made without an icon got "folder", which the frontend's icon
set (`frontend/src/lib/category-icons.ts`) does not have, so they showed a
generic one. They move to "circle-help" (the "Outros" icon), and so does the
column's server default. A data fix: the downgrade puts the default back but
cannot tell which groups had "folder", so it leaves the icons.

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
_GROUP_ICON = "circle-help"
_OLD_GROUP_ICON = "folder"
_FIX_GROUP_ICONS = (
    f"UPDATE category_groups SET icon = '{_GROUP_ICON}' WHERE icon = '{_OLD_GROUP_ICON}'"
)


def _in(kinds: tuple[str, ...]) -> str:
    return f"kind IN ({', '.join(repr(k) for k in kinds)})"


def upgrade() -> None:
    op.add_column("merchant_memory", sa.Column("note", sa.String(length=200), nullable=True))
    op.add_column(
        "merchant_memory", sa.Column("note_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint(_NAME, "categorization_runs", type_="check")
    op.create_check_constraint(_NAME, "categorization_runs", _in(_KINDS))
    op.alter_column("category_groups", "icon", server_default=_GROUP_ICON)
    op.execute(_FIX_GROUP_ICONS)


def downgrade() -> None:
    op.alter_column("category_groups", "icon", server_default=_OLD_GROUP_ICON)
    op.execute("DELETE FROM categorization_runs WHERE kind = 'ask'")
    op.drop_constraint(_NAME, "categorization_runs", type_="check")
    op.create_check_constraint(_NAME, "categorization_runs", _in(_KINDS[:-1]))
    op.drop_column("merchant_memory", "note_updated_at")
    op.drop_column("merchant_memory", "note")
