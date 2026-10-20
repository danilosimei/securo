"""provenance, memory, suggestions and runs for AI categorization

`transactions.category_source` records who set the category, so the
pipeline can tell a choice a person made (protected) from one a provider
or the model guessed (revisable). Five tables hold the rest: per-workspace
settings, the runs (and their cost), the change history, pending
suggestions and the per-merchant memory.

Data step: every transaction that already has a category is marked
`provider`. We cannot know which of them a person chose, and treating
them all as revisable is the safe side for a first pass: the pipeline
still never overwrites anything it is not confident about.

Revision ID: 097
Revises: 096
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None

_SOURCES = (
    "none", "user", "rule", "memory", "ai", "provider",
    "installment", "import", "recurring", "legacy",
)
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _id():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _ws():
    return sa.Column(
        "workspace_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )


def _fk(name, target, nullable=True, ondelete="SET NULL"):
    return sa.Column(
        name, postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete), nullable=nullable,
    )


def _ts(name, nullable=False):
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=None if nullable else sa.func.now(),
    )


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("category_source", sa.String(16), nullable=False, server_default="none"),
    )
    op.create_index(
        "ix_transactions_workspace_category_source",
        "transactions", ["workspace_id", "category_source"],
    )
    op.execute(
        "UPDATE transactions SET category_source = 'provider' WHERE category_id IS NOT NULL"
    )

    op.create_table(
        "categorization_settings",
        _id(), _ws(),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("auto_apply_threshold", sa.Integer, nullable=False, server_default="90"),
        sa.Column("monthly_cap_usd", sa.Numeric(10, 2), nullable=False, server_default="1.00"),
        _fk("llm_connection_id", "agent_llm_connections.id"),
        _fk("owner_user_id", "users.id"),
        sa.Column("provider_category_map", _JSON, nullable=False, server_default="{}"),
        _ts("last_run_at", nullable=True),
        sa.UniqueConstraint("workspace_id", name="uq_categorization_settings_workspace"),
        sa.CheckConstraint(
            "auto_apply_threshold BETWEEN 50 AND 100",
            name="ck_categorization_settings_threshold",
        ),
    )

    op.create_table(
        "categorization_runs",
        _id(), _ws(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("batch_id", sa.String(128), nullable=True),
        sa.Column("is_batch", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("stats", _JSON, nullable=False, server_default="{}"),
        sa.Column("detail", _JSON, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        _ts("created_at"),
        _ts("finished_at", nullable=True),
        sa.CheckConstraint(
            "kind IN ('classify', 'organize', 'redistribute')",
            name="ck_categorization_run_kind",
        ),
        sa.CheckConstraint(
            "trigger IN ('ingest', 'manual', 'backlog')",
            name="ck_categorization_run_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'processing_batch', 'done',"
            " 'failed', 'capped')",
            name="ck_categorization_run_status",
        ),
    )
    op.create_index(
        "ix_categorization_runs_workspace_created",
        "categorization_runs", ["workspace_id", "created_at"],
    )

    op.create_table(
        "categorization_events",
        _id(), _ws(),
        _fk("transaction_id", "transactions.id", nullable=False, ondelete="CASCADE"),
        _fk("run_id", "categorization_runs.id"),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("previous_source", sa.String(16), nullable=True),
        _fk("previous_category_id", "categories.id"),
        _fk("new_category_id", "categories.id"),
        _fk("previous_payee_id", "payees.id"),
        _fk("new_payee_id", "payees.id"),
        sa.Column("confidence", sa.Integer, nullable=True),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        _fk("user_id", "users.id"),
        _ts("created_at"),
        _ts("undone_at", nullable=True),
        sa.CheckConstraint(
            "source IN (" + ", ".join(f"'{s}'" for s in _SOURCES) + ")",
            name="ck_categorization_event_source",
        ),
    )
    op.create_index(
        "ix_categorization_events_workspace_created",
        "categorization_events", ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_categorization_events_transaction_created",
        "categorization_events", ["transaction_id", "created_at"],
    )
    op.create_index("ix_categorization_events_run", "categorization_events", ["run_id"])

    op.create_table(
        "categorization_suggestions",
        _id(), _ws(),
        _fk("transaction_id", "transactions.id", nullable=False, ondelete="CASCADE"),
        _fk("run_id", "categorization_runs.id"),
        sa.Column("merchant_key", sa.String(255), nullable=False),
        sa.Column("clean_name", sa.String(255), nullable=True),
        _fk("category_id", "categories.id"),
        _fk("alt_category_id", "categories.id"),
        sa.Column("confidence", sa.Integer, nullable=True),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("new_category", _JSON, nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        _ts("created_at"),
        _ts("resolved_at", nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="ck_categorization_suggestion_status",
        ),
    )
    op.create_index(
        "ix_categorization_suggestions_transaction_status",
        "categorization_suggestions", ["transaction_id", "status"],
    )
    op.create_index(
        "ix_categorization_suggestions_workspace_status",
        "categorization_suggestions", ["workspace_id", "status"],
    )

    op.create_table(
        "merchant_memory",
        _id(), _ws(),
        sa.Column("merchant_key", sa.String(255), nullable=False),
        sa.Column("counts", _JSON, nullable=False, server_default="{}"),
        _fk("last_user_category_id", "categories.id"),
        _fk("ai_category_id", "categories.id"),
        sa.Column("ai_confidence", sa.Integer, nullable=True),
        sa.Column("ai_clean_name", sa.String(255), nullable=True),
        _ts("updated_at"),
        sa.UniqueConstraint("workspace_id", "merchant_key", name="uq_merchant_memory_workspace_key"),
    )


def downgrade() -> None:
    op.drop_table("merchant_memory")
    op.drop_table("categorization_suggestions")
    op.drop_table("categorization_events")
    op.drop_table("categorization_runs")
    op.drop_table("categorization_settings")
    op.drop_index("ix_transactions_workspace_category_source", table_name="transactions")
    op.drop_column("transactions", "category_source")
