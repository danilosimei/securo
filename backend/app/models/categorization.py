"""Tables behind AI categorization: provenance, memory, suggestions, runs.

The point of this module is one question the app could not answer before:
*who put this category on this transaction?* `Transaction.category_source`
holds the answer for the current value, `CategorizationEvent` keeps the
history, and the pipeline uses both to decide what it may touch -- a
category a person chose is never overwritten, one a provider or the model
guessed can be revisited.

Everything is scoped to the workspace. FKs to `transactions` cascade (the
history of a deleted transaction has no reader); FKs to categories, payees
and users are SET NULL, so tidying the taxonomy or removing a person never
takes the audit trail with it.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base

_Json = JSON().with_variant(JSONB(), "postgresql")

#: Where a category came from. `user` and `legacy` are protected from the
#: pipeline; `none`, `provider`, `memory` and `ai` may be revisited.
CATEGORY_SOURCES = (
    "none", "user", "rule", "memory", "ai", "provider",
    "installment", "import", "recurring", "legacy",
)
#: Events also record reverts, with the pseudo-source `undo`.
EVENT_SOURCES = CATEGORY_SOURCES + ("undo",)


def _in(column: str, values: tuple[str, ...]) -> str:
    """A CHECK clause; also used by `Transaction.category_source` (098)."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _workspace_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )


def _set_null_fk(target: str) -> Mapped[Optional[uuid.UUID]]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete="SET NULL"), nullable=True
    )


class CategorizationSettings(Base):
    """How automatic categorization behaves for one workspace.

    One row per workspace. The threshold and the monthly cap are the two
    knobs that bound what the model may do on its own: how sure it must be
    to apply instead of suggest, and how much it may spend in a month.
    """

    __tablename__ = "categorization_settings"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_categorization_settings_workspace"),
        CheckConstraint(
            "auto_apply_threshold BETWEEN 50 AND 100",
            name="ck_categorization_settings_threshold",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    auto_apply_threshold: Mapped[int] = mapped_column(Integer, default=90, server_default="90")
    monthly_cap_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("1.00"), server_default="1.00"
    )
    llm_connection_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("agent_llm_connections.id")
    #: Whose API key and consent the runs use when no person is acting.
    owner_user_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("users.id")
    #: Bank-provider category name -> Securo category id, learned over time.
    provider_category_map: Mapped[dict[str, Any]] = mapped_column(
        _Json, default=dict, server_default="{}"
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CategorizationRun(Base):
    """One execution of the pipeline, with what it cost.

    Runs are the ledger the monthly cap is checked against, and the place
    the organizer keeps its proposal (`detail`) until a person accepts it.
    """

    __tablename__ = "categorization_runs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('classify', 'organize', 'redistribute')",
            name="ck_categorization_run_kind",
        ),
        CheckConstraint(
            "trigger IN ('ingest', 'manual', 'backlog')",
            name="ck_categorization_run_trigger",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'processing_batch', 'done',"
            " 'failed', 'capped')",
            name="ck_categorization_run_status",
        ),
        Index("ix_categorization_runs_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    kind: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_batch: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), default=Decimal("0"), server_default="0"
    )
    #: applied / suggested / memory / skipped counters.
    stats: Mapped[dict[str, Any]] = mapped_column(_Json, default=dict, server_default="{}")
    detail: Mapped[dict[str, Any]] = mapped_column(_Json, default=dict, server_default="{}")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CategorizationEvent(Base):
    """One change of a transaction's category or payee, and why.

    Carries the previous values so an undo is a lookup, not a guess.
    `undone_at` marks an event as reverted instead of deleting it: the
    history of a mistake is worth keeping.
    """

    __tablename__ = "categorization_events"
    __table_args__ = (
        CheckConstraint(_in("source", EVENT_SOURCES), name="ck_categorization_event_source"),
        Index("ix_categorization_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_categorization_events_transaction_created", "transaction_id", "created_at"),
        Index("ix_categorization_events_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE")
    )
    run_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categorization_runs.id")
    source: Mapped[str] = mapped_column(String(16))
    previous_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    previous_category_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categories.id")
    new_category_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categories.id")
    previous_payee_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("payees.id")
    new_payee_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("payees.id")
    confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: Null means the pipeline acted on its own.
    user_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("users.id")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    undone_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CategorizationSuggestion(Base):
    """A category the model proposed but was not sure enough to apply.

    At most one pending suggestion per transaction is a rule of the
    service, not of the schema: a partial unique index would not be
    portable to the SQLite test database.
    """

    __tablename__ = "categorization_suggestions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="ck_categorization_suggestion_status",
        ),
        Index("ix_categorization_suggestions_transaction_status", "transaction_id", "status"),
        Index("ix_categorization_suggestions_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE")
    )
    run_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categorization_runs.id")
    merchant_key: Mapped[str] = mapped_column(String(255))
    clean_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categories.id")
    alt_category_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categories.id")
    confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    #: `{group, name}` when the model proposes a category that does not exist.
    new_category: Mapped[Optional[dict[str, Any]]] = mapped_column(_Json, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class MerchantMemory(Base):
    """What this workspace has taught the system about a merchant.

    `counts` is category id -> how many times a person picked it. The
    pipeline reuses a choice only when it is both repeated and dominant,
    so one stray correction does not become policy.
    """

    __tablename__ = "merchant_memory"
    __table_args__ = (
        UniqueConstraint("workspace_id", "merchant_key", name="uq_merchant_memory_workspace_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    merchant_key: Mapped[str] = mapped_column(String(255))
    counts: Mapped[dict[str, Any]] = mapped_column(_Json, default=dict, server_default="{}")
    last_user_category_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categories.id")
    ai_category_id: Mapped[Optional[uuid.UUID]] = _set_null_fk("categories.id")
    ai_confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ai_clean_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
