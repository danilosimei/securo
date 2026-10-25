"""categorization event action

`categorization_events.action_id` ties together the events of one action (a
click, a run): Categorization > Histórico shows one line per action and
undoes it whole. New events get it from the request (`X-Action-Id`), the run
or the batch.

Events written before it are grouped the closest way the data allows: a
run's events by the run; the rest by who wrote them, their origin and the
second they were written, and, except for undos, the category they set.
The id is a hash of that key, so the backfill is deterministic.

Revision ID: 103
Revises: 102
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("categorization_events", sa.Column("action_id", UUID(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE categorization_events
        SET action_id = COALESCE(
            run_id,
            md5(
                workspace_id::text
                || '|' || COALESCE(user_id::text, '')
                || '|' || source
                || '|' || to_char(date_trunc('second', created_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')
                || '|' || CASE WHEN source = 'undo' THEN '' ELSE COALESCE(new_category_id::text, '') END
            )::uuid
        )
        """
    )
    op.alter_column("categorization_events", "action_id", nullable=False)
    op.create_index(
        "ix_categorization_events_workspace_action",
        "categorization_events",
        ["workspace_id", "action_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_categorization_events_workspace_action", table_name="categorization_events")
    op.drop_column("categorization_events", "action_id")
