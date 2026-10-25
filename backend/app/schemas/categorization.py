"""Request and response bodies of the categorization feature."""
import uuid
from datetime import date as _Date
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class CategorizationSettingsUpdate(BaseModel):
    """A partial update: fields left out stay as they are.

    `llm_connection_id` is the only blankable field (null = fall back to the
    owner's default connection); a null anywhere else means "unchanged".
    """

    enabled: Optional[bool] = None
    auto_apply_threshold: Optional[int] = Field(default=None, ge=50, le=100)
    monthly_cap_usd: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("1000"), decimal_places=2)
    llm_connection_id: Optional[uuid.UUID] = None


class CategorizationSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    auto_apply_threshold: int
    monthly_cap_usd: Decimal
    llm_connection_id: Optional[uuid.UUID] = None
    owner_user_id: Optional[uuid.UUID] = None
    last_run_at: Optional[datetime] = None


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    trigger: str
    status: str
    stats: dict[str, Any] = Field(default_factory=dict)
    cost_usd: Decimal = Decimal("0")
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class RunRequest(BaseModel):
    scope: Literal["backlog"] = "backlog"


class OrganizerImpact(BaseModel):
    """What an op touches in the last 12 months (recomputed, not the model's)."""

    tx: int = 0
    total: float = 0.0


class OrganizerOp(BaseModel):
    """One proposed change to the category tree.

    `target`/`group`/`into` are the proposal's short ids (or a new group's
    name); `labels` carries the readable names to show instead.
    """

    id: str
    op: str
    target: Optional[str] = None
    group: Optional[str] = None
    name: Optional[str] = None
    children: list[str] = Field(default_factory=list)
    into: Optional[str] = None
    why: str = ""
    impact: OrganizerImpact = Field(default_factory=OrganizerImpact)
    labels: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "applied", "rejected"] = "pending"
    decided_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    #: An applied split's redistribution run (latest attempt), on GET only.
    redistribution: Optional[RunRead] = None


class OrganizerRead(BaseModel):
    """The latest organize run, its ops and, when it has no proposal, why."""

    run: Optional[RunRead] = None
    ops: list[OrganizerOp] = Field(default_factory=list)
    message: Optional[str] = None


class OrganizerApplyResult(BaseModel):
    op: OrganizerOp
    #: The redistribution a split queued, to follow by polling.
    redistribute_run: Optional[RunRead] = None


class LastRun(BaseModel):
    id: uuid.UUID
    status: str
    finished_at: Optional[datetime] = None
    stats: dict[str, Any] = Field(default_factory=dict)


class CategorizationSummary(BaseModel):
    pending_groups: int
    pending_transactions: int
    applied_last_7d: int
    last_run: Optional[LastRun] = None
    ai_available: bool
    #: The workspace's "categorize with AI" switch.
    ai_enabled: bool
    month_cost_usd: Decimal
    monthly_cap_usd: Decimal


class ReviewSuggestion(BaseModel):
    category_id: Optional[uuid.UUID] = None
    alt_category_id: Optional[uuid.UUID] = None
    confidence: Optional[int] = None
    reason: Optional[str] = None


class NewCategory(BaseModel):
    group: str
    name: str


class ReviewTransaction(BaseModel):
    id: uuid.UUID
    date: _Date
    description: str
    #: As the bank sent it, when the description was rewritten since.
    original_description: Optional[str] = None
    #: Unmasked: the frontend masks it.
    amount: Decimal
    currency: str
    #: `debit` or `credit`.
    type: str


class NoteRequest(BaseModel):
    merchant_key: str = Field(min_length=1, max_length=255)
    #: Cleaned and cut to `memory.NOTE_LENGTH` on save; empty clears it.
    note: Optional[str] = Field(default=None, max_length=400)


class NoteRead(BaseModel):
    merchant_key: str
    note: Optional[str] = None


class ReviewGroup(BaseModel):
    merchant_key: str
    clean_name: Optional[str] = None
    count: int
    #: Rows of `count` with no category: "Ignorar" would leave them so.
    uncategorized: int = 0
    #: Sum of the absolute amounts, unmasked: the frontend masks it.
    total: Decimal
    samples: list[str]
    suggestion: Optional[ReviewSuggestion] = None
    new_category: Optional[NewCategory] = None
    is_person: bool
    #: What the person said about the store, when they did.
    note: Optional[str] = None
    transaction_ids: list[uuid.UUID]
    #: The first rows of `transaction_ids` (at most `review.REVIEW_PREVIEW`).
    transactions: list[ReviewTransaction] = Field(default_factory=list)


class NewCategoryProposal(BaseModel):
    group: str
    name: str
    merchant_keys: list[str]
    count: int
    total: Decimal


class ReviewQueue(BaseModel):
    groups: list[ReviewGroup]
    #: Stores pending in all, not only on this page.
    total: int
    new_category_proposals: list[NewCategoryProposal]


class AcceptRequest(BaseModel):
    merchant_key: str = Field(min_length=1, max_length=255)
    category_id: uuid.UUID
    #: Only these rows of the store; all its pending rows when left out.
    transaction_ids: Optional[list[uuid.UUID]] = None


class AppliedResult(BaseModel):
    applied: int
    #: The events written, for "undo" (`POST /undo {event_ids}`).
    event_ids: list[uuid.UUID]
    #: The rows that changed, in order (the first is where "similar" starts).
    transaction_ids: list[uuid.UUID]
    #: Always null: a person's action is not a run. Kept for the contract;
    #: undo goes through `event_ids`.
    event_run_id: Optional[uuid.UUID] = None


class SimilarAppliedResult(AppliedResult):
    #: The shown rows (`transaction_ids` of the request) left as they were:
    #: they changed since they were shown, in the order given.
    skipped: list[uuid.UUID] = Field(default_factory=list)


class DismissRequest(BaseModel):
    merchant_key: str = Field(min_length=1, max_length=255)


class DismissResult(BaseModel):
    dismissed: int


class CreateCategoryRequest(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    merchant_keys: list[str] = Field(default_factory=list, max_length=500)


class CreateCategoryResult(BaseModel):
    category_id: uuid.UUID
    applied: int
    event_ids: list[uuid.UUID]
    #: The rows that changed, in order.
    transaction_ids: list[uuid.UUID]


#: A name with something besides spaces in it, stripped.
CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class NewCategoryDraft(BaseModel):
    group: CategoryName
    name: CategoryName


class ChooseRequest(BaseModel):
    """One choice for a store: exactly one of `category_id` and `new_category`.

    Without `transaction_ids`, the store's pending rows (Revisar); with them,
    exactly those rows. `note` left out leaves the store's note as it is;
    null (or empty) clears it.
    """

    merchant_key: str = Field(min_length=1, max_length=255)
    category_id: Optional[uuid.UUID] = None
    new_category: Optional[NewCategoryDraft] = None
    transaction_ids: Optional[list[uuid.UUID]] = Field(default=None, min_length=1, max_length=500)
    note: Optional[str] = Field(default=None, max_length=400)


class ChooseResult(BaseModel):
    category_id: uuid.UUID
    created_group: bool
    created_category: bool
    applied: int
    #: The events written, for "undo" (`POST /undo {event_ids}`).
    event_ids: list[uuid.UUID]
    #: The rows that changed, in order.
    transaction_ids: list[uuid.UUID]


class AskMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    """One message of the conversation about a store: the whole conversation
    so far (kept by the client, never stored), ending with the person's.

    Without `transaction_ids`, about the store's pending rows (Revisar); with
    them, about exactly those rows (Aplicadas).
    """

    merchant_key: str = Field(min_length=1, max_length=255)
    transaction_ids: Optional[list[uuid.UUID]] = Field(default=None, min_length=1, max_length=500)
    messages: list[AskMessage] = Field(min_length=1, max_length=80)


class AskSuggestionRead(BaseModel):
    #: An existing category, or null with `new_category`.
    category_id: Optional[uuid.UUID] = None
    #: A category to create on Accept (`POST /choose new_category`).
    new_category: Optional[NewCategory] = None
    #: Whether the group of `new_category` already exists.
    group_exists: bool
    confidence: int
    #: Rows an Accept would file.
    applies_to: int


class AskResponse(BaseModel):
    reply: str
    suggestion: Optional[AskSuggestionRead] = None
    #: The store's whole note, updated; null leaves the saved one as it is.
    note: Optional[str] = None
    #: Send it back as the next `assistant` message.
    assistant_turn: str
    person_messages_left: int


class AppliedTransaction(BaseModel):
    id: uuid.UUID
    date: _Date
    description: str
    amount: Decimal
    currency: str


class AppliedEvent(BaseModel):
    #: None for a row listed by origin with no standing event (no undo).
    event_id: Optional[uuid.UUID] = None
    run_id: Optional[uuid.UUID] = None
    transaction: AppliedTransaction
    source: str
    previous_category_id: Optional[uuid.UUID] = None
    new_category_id: Optional[uuid.UUID] = None
    confidence: Optional[int] = None
    reason: Optional[str] = None
    #: The event taught (or took back) one of the store's picks: undoing it
    #: changes the memory even when the category stays (a person confirming
    #: a guess).
    memory_pick: bool = False
    created_at: datetime
    #: Grouping id of the store; never shown to people.
    merchant_key: str
    #: What to call the store on screen.
    store_name: str


class AppliedPage(BaseModel):
    items: list[AppliedEvent]
    total: int


class Scoreboard(BaseModel):
    """Categorizable rows (posted, no transfer pair, no opening balance, not
    ignored); `decided + to_review + waiting == total`."""

    total: int
    #: With a category and nothing pending to review.
    decided: int
    #: With a pending suggestion Revisar shows.
    to_review: int
    #: No category and nothing to review yet.
    waiting: int


class UndoRequest(BaseModel):
    event_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=5000)
    run_id: Optional[uuid.UUID] = None


class UndoResultRead(BaseModel):
    restored: int
    skipped: int


class ProvenanceEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: Optional[uuid.UUID] = None
    source: str
    previous_source: Optional[str] = None
    previous_category_id: Optional[uuid.UUID] = None
    new_category_id: Optional[uuid.UUID] = None
    confidence: Optional[int] = None
    reason: Optional[str] = None
    model: Optional[str] = None
    user_id: Optional[uuid.UUID] = None
    created_at: datetime
    undone_at: Optional[datetime] = None


class Provenance(BaseModel):
    current_source: str
    original_description: Optional[str] = None
    events: list[ProvenanceEvent]
    #: The note on the row's store; none when it has no note.
    store_note: Optional[NoteRead] = None


class SimilarRequest(BaseModel):
    transaction_id: uuid.UUID
    #: Categorization section: leave rows a rule filed out of the set.
    exclude_rule: bool = False
    #: Only `/similar/apply`: apply just these (the rows the person was shown).
    transaction_ids: Optional[list[uuid.UUID]] = Field(None, max_length=5000)


class SimilarTransaction(BaseModel):
    id: uuid.UUID
    date: _Date
    description: str
    amount: Decimal
    currency: str
    type: str
    category_id: Optional[uuid.UUID] = None
    category_source: str


class SimilarResult(BaseModel):
    merchant_key: str
    count: int
    transaction_ids: list[uuid.UUID]
    #: The same N rows, in the same order as `transaction_ids`.
    transactions: list[SimilarTransaction]


class RuleDraftCondition(BaseModel):
    field: str
    op: str
    value: str


class RuleDraftAction(BaseModel):
    op: str
    value: str


class RuleDraft(BaseModel):
    """A rule pre-filled from the merchant key, in `RuleCreate`'s shape."""

    name: str
    conditions: list[RuleDraftCondition]
    actions: list[RuleDraftAction]
