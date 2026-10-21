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

The events written before it are marked as the 097 code counted them, so
undoing an old choice still takes its pick back (an unmarked choice would
leave a pick for a decision the person undid):
- an accept taught one pick per store key per call, and its suggestions
  share one `resolved_at`: one `user` event per (workspace, key,
  `resolved_at`) is marked, the latest written just before it;
- every other standing person choice (a form edit, a bulk edit, apply to
  similar) is marked, and so is an accept's event written 2 minutes or more
  before its `resolved_at` (outside the window, it falls to this rule). The
  data cannot tell which row of an old bulk edit taught the store, so all
  of them are marked: undoing an old bulk row by row may take back more
  picks than the bulk taught, since the memory counts per store and the
  extra ones come from the store's other decisions (floored at 0, and
  bounded by the few such events production holds);
- clears stay unmarked: marking one that took nothing would give back a
  pick on undo.

The marks are derived from the data, not kept: a downgrade drops them with
the column, and upgrading again derives them anew from the events and
suggestions as they stand then. Marks the live code set after 098 are not
restored as they were (a clear's mark is lost; every row of a post-098 bulk
edit is marked), which errs the same way as above.

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
#: A person's standing choice of a category (the events the undo unlearns).
_CHOICE = (
    "e.source = 'user' AND e.user_id IS NOT NULL"
    " AND e.new_category_id IS NOT NULL AND e.undone_at IS NULL"
)
#: The accepted suggestion an event was written for: same row, just before
#: the call stamped `resolved_at`.
_ACCEPTED_FOR = (
    "s.status = 'accepted' AND s.resolved_at IS NOT NULL"
    " AND s.workspace_id = e.workspace_id AND s.transaction_id = e.transaction_id"
    " AND e.created_at <= s.resolved_at"
    " AND e.created_at > s.resolved_at - interval '2 minutes'"
)
_MARK_ACCEPTS = f"""
UPDATE categorization_events SET memory_pick = true
WHERE id IN (
    SELECT DISTINCT ON (s.workspace_id, s.merchant_key, s.resolved_at) e.id
    FROM categorization_suggestions s
    JOIN categorization_events e ON {_ACCEPTED_FOR}
    WHERE {_CHOICE}
    ORDER BY s.workspace_id, s.merchant_key, s.resolved_at, e.created_at DESC, e.id
)
"""
_MARK_OTHER_PICKS = f"""
UPDATE categorization_events e SET memory_pick = true
WHERE {_CHOICE}
  AND NOT EXISTS (SELECT 1 FROM categorization_suggestions s WHERE {_ACCEPTED_FOR})
"""
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
    op.execute(_MARK_ACCEPTS)
    op.execute(_MARK_OTHER_PICKS)


def downgrade() -> None:
    op.drop_column("categorization_events", "memory_pick")
    op.drop_constraint(_NAME, "transactions", type_="check")
