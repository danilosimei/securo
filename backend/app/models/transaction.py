import uuid
from datetime import date as _date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, cast

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    event,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.categorization import CATEGORY_SOURCES, in_clause

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.category import Category
    from app.models.credit_card_bill import CreditCardBill
    from app.models.import_log import ImportLog
    from app.models.payee import Payee
    from app.models.recurring_transaction import RecurringTransaction
    from app.models.transaction_attachment import TransactionAttachment
    from app.models.transaction_split import TransactionSplit


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_workspace_category_source", "workspace_id", "category_source"),
        Index("ix_transactions_ws_merchant", "workspace_id", "merchant_key"),
        CheckConstraint(
            in_clause("category_source", CATEGORY_SOURCES), name="ck_transaction_category_source"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True)
    # Who set `category_id`: none, user, rule, memory, ai, provider,
    # installment, import, recurring or legacy. The categorization pipeline
    # only revisits none/provider/memory/ai; user and legacy are protected.
    category_source: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # Provider's transaction ID
    description: Mapped[str] = mapped_column(String(500))
    # Store key of the categorization (`merchant_key`, as the memory stores it),
    # kept in step by the `_refresh_merchant_key` hook below.
    merchant_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    original_description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description_is_rule_managed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    date: Mapped[_date] = mapped_column(Date)
    # Effective date for cash-flow reporting. For regular accounts this equals
    # `date`. For credit card transactions it's the due date of the bill that
    # the transaction belongs to — so accrual-mode aggregations count the
    # purchase when it hits the user's cash, not when it was made.
    effective_date: Mapped[_date] = mapped_column(Date, index=True)
    type: Mapped[str] = mapped_column(String(10))  # debit, credit
    source: Mapped[str] = mapped_column(String(20))  # sync, ofx, csv, manual
    status: Mapped[str] = mapped_column(String(10), default="posted")  # posted, pending
    payee: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    payee_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("payees.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    import_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("import_logs.id"), nullable=True)
    transfer_pair_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    # True on both legs of a cross-currency transfer whose destination amount
    # the user typed in, instead of letting it be converted at the market rate.
    # Both amounts are then observed facts, so editing one leg must not
    # re-derive the other from an FX rate.
    transfer_amount_explicit: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    amount_primary: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=15, scale=2), nullable=True)
    fx_rate_used: Mapped[Optional[Decimal]] = mapped_column(Numeric(precision=20, scale=10), nullable=True)
    # Installment (parcelamento) metadata. Populated from provider data when available.
    # `installment_number` is 1-indexed (e.g. 3 for "3/12"). Storing alongside the
    # raw tx row so the door stays open to a plan view or manual entry later
    # without another migration.
    installment_number: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    total_installments: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    installment_total_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(precision=15, scale=2), nullable=True
    )
    installment_purchase_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    # Stable identity for rows created by the manual installment-series
    # endpoint. Provider-synced installment rows leave this null because their
    # metadata is only a deduplication fingerprint, not a series identifier.
    installment_series_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # User-set manual override for which bill cycle this tx belongs to. Null
    # by default; only meaningful for credit-card accounts. When set, beats
    # both Pluggy's billId and the cycle-math fallback (issue #92, the
    # LucasFidelis cycle_override request).
    effective_bill_date: Mapped[Optional[_date]] = mapped_column(Date, nullable=True)
    # FK to the bill this transaction belongs to (issue #92). Set during sync
    # for providers that expose a bills feed; null otherwise — in which case
    # the read path falls back to locally-computed cycle math via
    # apply_effective_date. ON DELETE SET NULL: if a bill row is removed
    # (re-sync, deletion), the transaction stays put and just unlinks.
    bill_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("credit_card_bills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Ignore the row everywhere monetary totals are calculated, including the
    # account balance. The row remains stored and can still be shown in ledger
    # views unless the caller explicitly hides ignored transactions.
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Keep the row in the ledger and current balance, but leave it out of P&L
    # reports. This differs from is_ignored, which also removes the row from
    # balance calculations. Balance adjustments use this distinction so the
    # reconciled balance stays exact without looking like income or spending.
    exclude_from_pnl: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # Link to the recurring bill this transaction fulfills (issue #116). Set when
    # a synced/imported/manual charge is matched to a recurring bill, or stamped
    # onto the placeholder generate_pending materializes. ON DELETE SET NULL: if
    # the recurring bill is removed, the transaction stays put and just unlinks.
    recurring_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recurring_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    account: Mapped["Account"] = relationship(back_populates="transactions")
    category: Mapped[Optional["Category"]] = relationship()
    bill: Mapped[Optional["CreditCardBill"]] = relationship()
    payee_entity: Mapped[Optional["Payee"]] = relationship(back_populates="transactions")
    recurring_transaction: Mapped[Optional["RecurringTransaction"]] = relationship(
        back_populates="transactions"
    )
    import_log: Mapped[Optional["ImportLog"]] = relationship(back_populates="transactions")
    attachments: Mapped[list["TransactionAttachment"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    splits: Mapped[list["TransactionSplit"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )

    # Populated dynamically by the service (not DB columns).
    is_shared: bool = False
    viewer_share = cast(Optional[Decimal], None)
    group_id = cast(Optional[uuid.UUID], None)
    parent_owner_name = cast(Optional[str], None)
    attachment_count: int = 0
    payee_name = cast(Optional[str], None)


# Safety net: `effective_date` is NOT NULL. For non-CC transactions it always
# equals `date`, so if a call site (or test) forgets to set it, fall back
# silently. CC-aware service code still calls `apply_effective_date` explicitly
# so that cycle-based due dates are stored when the account has the metadata.
@event.listens_for(Transaction, "before_insert")
def _default_effective_date(mapper, connection, target):
    if target.effective_date is None:
        target.effective_date = target.date


@event.listens_for(Transaction, "before_update")
def _default_effective_date_on_update(mapper, connection, target):
    if target.effective_date is None:
        target.effective_date = target.date


def _payee_of(target, connection) -> tuple[Optional[str], Optional[str]]:
    """(name, source) of the row's payee, with no extra query when the payee
    is already loaded. The hook runs inside the flush, so a lookup goes
    through the flush's own connection."""
    if target.payee_id is None:
        return None, None
    from sqlalchemy import inspect as sa_inspect, select as sa_select

    from app.models.payee import Payee

    state = sa_inspect(target)
    if "payee_entity" in state.dict and target.payee_entity is not None:
        return target.payee_entity.name, target.payee_entity.source
    session = state.session
    if session is not None:
        loaded = session.identity_map.get(sa_inspect(Payee).identity_key_from_primary_key((target.payee_id,)))
        if loaded is not None:
            return loaded.name, loaded.source
    row = connection.execute(
        sa_select(Payee.name, Payee.source).where(Payee.id == target.payee_id)
    ).first()
    return (row[0], row[1]) if row is not None else (None, None)


def _refresh_merchant_key(mapper, connection, target):
    """Keep `merchant_key` equal to the categorization's key for the row.

    Recomputed on insert and when `description` or `payee_id` changed. Not
    covered: a Payee renamed (or re-sourced) afterwards, and Core `update()`
    statements that bypass the unit of work (payee delete/merge); the
    backfill script (`scripts/backfill_merchant_keys.py`) repairs both.
    """
    from sqlalchemy import inspect as sa_inspect

    state = sa_inspect(target)
    if (
        target.merchant_key is not None
        and not state.attrs.description.history.has_changes()
        and not state.attrs.payee_id.history.has_changes()
    ):
        return
    from app.services.categorization.memory import stored_key
    from app.services.categorization.merchant_key import merchant_key

    name, source = _payee_of(target, connection)
    target.merchant_key = stored_key(
        merchant_key(target.description, payee_name=name, payee_source=source)
    )


event.listens_for(Transaction, "before_insert")(_refresh_merchant_key)
event.listens_for(Transaction, "before_update")(_refresh_merchant_key)
