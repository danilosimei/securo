"""Spending broken down by group, by category, by store and by account.

The levels share everything but the row a transaction falls into, so a level
is one function from "category" (or store key, or account) to "row"; the
sums, the comparison period and the sparkline are the same for all of them.
"""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.categorization import CategorizationSuggestion, MerchantMemory
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.services.reports.merchant_labels import readable_label
from app.services.reports.periods import Period, _month_end, _shift_months
from app.services.reports.rules import (
    CENT,
    KIND_INCOME,
    KIND_NORMAL,
    ZERO,
    RangeTotals,
    SegmentFlows,
    collect_flows,
    collect_split_flows,
    load_category_kinds,
    spend_of,
    totals_from,
)

LEVELS = ("group", "category", "merchant", "account")
SPARKLINE_MONTHS = 12

# The two rows a level can need that no real group/category stands for.
# Fixed pt-BR labels, as the dashboard does for its "Sem categoria".
NO_GROUP_LABEL = "Sem grupo"
NO_CATEGORY_LABEL = "Sem categoria"
NO_ACCOUNT_LABEL = "Conta sem nome"


@dataclass(frozen=True)
class RowRef:
    id: Optional[str]
    label: str
    kind: str
    #: Only on the "all categories" listing: the group the category sits in.
    group_label: Optional[str] = None


#: category id (None = no category) or store key -> the row its spending lands
#: in; None leaves it out of the level.
RowOf = Callable[[Any], Optional[RowRef]]


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


async def _group_level(session: AsyncSession, workspace_id: uuid.UUID) -> RowOf:
    rows = (
        await session.execute(
            select(Category.id, Category.group_id, Category.name, Category.report_kind).where(
                Category.workspace_id == workspace_id
            )
        )
    ).all()
    categories = {cid: gid for cid, gid, _, _ in rows}
    # A refund category gets its own line wherever it is filed: folded into
    # its group, the reduction would hide inside that group's total.
    refunds = {cid: name for cid, _, name, kind in rows if kind == "refund"}
    groups = {
        gid: name
        for gid, name in (
            await session.execute(
                select(CategoryGroup.id, CategoryGroup.name).where(
                    CategoryGroup.workspace_id == workspace_id
                )
            )
        ).all()
    }

    def row_of(category_id: Any) -> Optional[RowRef]:
        if category_id is None:
            return RowRef(None, NO_CATEGORY_LABEL, "group")
        if category_id in refunds:
            return RowRef(str(category_id), refunds[category_id], "category")
        group_id = categories.get(category_id)
        if group_id is None or group_id not in groups:
            return RowRef(None, NO_GROUP_LABEL, "group")
        return RowRef(str(group_id), groups[group_id], "group")

    return row_of


async def _category_level(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    group_id: Optional[uuid.UUID],
    no_group: bool,
    all_groups: bool = False,
) -> RowOf:
    """The categories of one group (or of no group, or of every group with
    `all_groups`, each carrying its group's name). Income categories are not
    spending and refund categories have their own line on the group level, so
    neither belongs to the group's total."""
    rows = (
        await session.execute(
            select(Category.id, Category.group_id, Category.name, Category.report_kind).where(
                Category.workspace_id == workspace_id
            )
        )
    ).all()
    group_names = {
        gid: name
        for gid, name in (
            await session.execute(
                select(CategoryGroup.id, CategoryGroup.name).where(
                    CategoryGroup.workspace_id == workspace_id
                )
            )
        ).all()
    }
    members: dict[uuid.UUID, RowRef] = {}
    for cid, gid, name, kind in rows:
        if kind != KIND_NORMAL:
            continue
        ungrouped = gid is None or gid not in group_names
        if all_groups:
            members[cid] = RowRef(
                str(cid), name, "category", NO_GROUP_LABEL if ungrouped else group_names[gid]
            )
        elif (ungrouped if no_group else gid == group_id):
            members[cid] = RowRef(str(cid), name, "category")

    def row_of(category_id: Any) -> Optional[RowRef]:
        return members.get(category_id)

    return row_of


async def _store_names(
    session: AsyncSession, workspace_id: uuid.UUID, keys: set[str]
) -> dict[str, str]:
    """Clean names the workspace already has for these store keys: the memory's
    first, then the latest suggestion's. Two queries, whatever the keys."""
    if not keys:
        return {}
    names: dict[str, str] = {}
    suggestions = await session.execute(
        select(CategorizationSuggestion.merchant_key, CategorizationSuggestion.clean_name)
        .where(
            CategorizationSuggestion.workspace_id == workspace_id,
            CategorizationSuggestion.merchant_key.in_(keys),
            CategorizationSuggestion.clean_name.is_not(None),
        )
        .order_by(CategorizationSuggestion.created_at)
    )
    for key, name in suggestions.all():
        if name and name.strip():
            names[key] = name.strip()  # later rows win
    memories = await session.execute(
        select(MerchantMemory.merchant_key, MerchantMemory.ai_clean_name).where(
            MerchantMemory.workspace_id == workspace_id,
            MerchantMemory.merchant_key.in_(keys),
            MerchantMemory.ai_clean_name.is_not(None),
        )
    )
    for key, name in memories.all():
        if name and name.strip():
            names[key] = name.strip()
    return names


def _store_totals(
    segments: list[SegmentFlows], scope_kind: Optional[str], scope_is_category: bool
) -> RangeTotals:
    """Spending per store key. Without a category only debits are spending,
    as on the group level; credits stay out."""
    spend: dict[Any, Decimal] = {}
    count: dict[Any, int] = {}
    unconverted = 0
    for seg in segments:
        unconverted += seg.unconverted_count
        for key, flow in seg.by_category.items():
            value = spend_of(flow, scope_kind) if scope_is_category else flow.debit
            spend[key] = spend.get(key, ZERO) + value
            count[key] = count.get(key, 0) + flow.count
    return RangeTotals(
        spend_by_category={k: v.quantize(CENT) for k, v in spend.items()},
        count_by_category=count,
        income_by_category={},
        uncategorized_income=ZERO,
        unconverted_count=unconverted,
    )


def _spend_by_row(totals: RangeTotals, row_of: RowOf) -> dict[RowRef, Decimal]:
    out: dict[RowRef, Decimal] = {}
    for category_id, value in totals.spend_by_category.items():
        ref = row_of(category_id)
        if ref is None:
            continue
        out[ref] = out.get(ref, ZERO) + value
    return out


def _count_by_row(totals: RangeTotals, row_of: RowOf) -> dict[RowRef, int]:
    out: dict[RowRef, int] = {}
    for category_id, count in totals.count_by_category.items():
        if category_id in totals.spend_by_category:  # income categories are not spending
            ref = row_of(category_id)
            if ref is None:
                continue
            out[ref] = out.get(ref, 0) + count
    return out


def _month_segments(period: Period) -> list[tuple]:
    last_month = period.to.replace(day=1)
    out = []
    for back in range(SPARKLINE_MONTHS - 1, -1, -1):
        start = _shift_months(last_month, -back)
        out.append((start, min(_month_end(start), period.to)))
    return out


def _split_totals(segments: list[dict[Any, SegmentFlows]], kinds: dict) -> list[RangeTotals]:
    """Spending per split key (account or store) of each segment, by the
    same rules as the group level: a category's debits minus its credits,
    debits only without a category, income categories left out."""
    out = []
    for seg in segments:
        spend: dict[Any, Decimal] = {}
        count: dict[Any, int] = {}
        unconverted = 0
        for split, flows in seg.items():
            unconverted += flows.unconverted_count
            totals = totals_from([flows], kinds)
            if not totals.spend_by_category:
                continue
            spend[split] = totals.spending
            count[split] = sum(totals.count_by_category[c] for c in totals.spend_by_category)
        out.append(
            RangeTotals(
                spend_by_category=spend,
                count_by_category=count,
                income_by_category={},
                uncategorized_income=ZERO,
                unconverted_count=unconverted,
            )
        )
    return out


def _main_category(flows: dict[Any, SegmentFlows], kinds: dict) -> dict[Any, Optional[uuid.UUID]]:
    """Store key -> the category where the store weighs most in the period."""
    out: dict[Any, Optional[uuid.UUID]] = {}
    for key, seg in flows.items():
        spend = totals_from([seg], kinds).spend_by_category
        if spend:
            out[key] = max(spend, key=lambda c: (spend[c], str(c)))
    return out


async def _account_names(session: AsyncSession, workspace_id: uuid.UUID) -> dict[Any, str]:
    rows = await session.execute(
        select(Account.id, Account.name, Account.display_name).where(
            Account.workspace_id == workspace_id
        )
    )
    return {aid: (display or name) for aid, name, display in rows.all()}


async def _category_names(session: AsyncSession, workspace_id: uuid.UUID) -> dict[Any, str]:
    rows = await session.execute(
        select(Category.id, Category.name).where(Category.workspace_id == workspace_id)
    )
    return {cid: name for cid, name in rows.all()}


async def breakdown(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    period: Period,
    level: str,
    group_id: Optional[uuid.UUID] = None,
    no_group: bool = False,
    all_groups: bool = False,
    category_id: Optional[uuid.UUID] = None,
    no_category: bool = False,
    all_categories: bool = False,
    account_id: Optional[uuid.UUID] = None,
) -> dict:
    if level not in LEVELS:
        raise _bad_request("level_unknown", f"Unknown breakdown level '{level}'")
    if level == "category" and group_id is None and not no_group and not all_groups:
        raise _bad_request("group_required", "The category level needs a group_id")
    if level == "merchant" and category_id is None and not no_category and not all_categories:
        raise _bad_request("category_required", "The store level needs a category_id")

    kinds = await load_category_kinds(session, workspace_id)
    account_ids = [account_id] if account_id is not None else None
    flow_kwargs: dict[str, Any] = {
        "workspace_id": workspace_id,
        "primary_currency": primary_currency,
        "account_ids": account_ids,
    }
    segments = [(period.from_, period.to), (period.previous_from, period.previous_to)]
    month_segments = _month_segments(period)
    row_of: RowOf
    main_category: dict[Any, Optional[uuid.UUID]] = {}
    category_names: dict[Any, str] = {}
    if level == "account" or (level == "merchant" and all_categories):
        split_by = "account" if level == "account" else "store"
        current_flows, previous_flows = await collect_split_flows(
            session, segments=segments, split_by=split_by, **flow_kwargs
        )
        monthly = await collect_split_flows(
            session, segments=month_segments, split_by=split_by, **flow_kwargs
        )
        current, previous = _split_totals([current_flows, previous_flows], kinds)
        monthly_totals = _split_totals(monthly, kinds)
        if level == "account":
            accounts = await _account_names(session, workspace_id)

            def row_of(key: Any) -> Optional[RowRef]:
                return RowRef(str(key), accounts.get(key, NO_ACCOUNT_LABEL), "account")
        else:
            main_category = _main_category(current_flows, kinds)
            category_names = await _category_names(session, workspace_id)
            keys: set[str] = {k for k in current.count_by_category if isinstance(k, str)}
            names = await _store_names(session, workspace_id, keys)

            def row_of(key: Any) -> Optional[RowRef]:
                store = key if isinstance(key, str) else None
                return RowRef(store, names.get(key) or readable_label(store), "merchant")
    elif level == "merchant":
        scope = (None if no_category else category_id,)
        scope_kind = None if no_category else kinds.get(category_id, KIND_NORMAL)
        if scope_kind == KIND_INCOME:  # income is not spending: nothing to list
            scope = (uuid.UUID(int=0),)

        def fold(seg_flows: list[SegmentFlows]) -> RangeTotals:
            return _store_totals(seg_flows, scope_kind, not no_category)

        current_flows, previous_flows = await collect_flows(
            session, segments=segments, store_scope=scope, user_id=user_id, **flow_kwargs
        )
        monthly = await collect_flows(
            session, segments=month_segments, store_scope=scope, user_id=user_id, **flow_kwargs
        )
        current = fold([current_flows])
        previous = fold([previous_flows])
        monthly_totals = [fold([seg]) for seg in monthly]
        keys: set[str] = {k for k in current.count_by_category if isinstance(k, str)}
        names = await _store_names(session, workspace_id, keys)

        def row_of(key: Any) -> Optional[RowRef]:
            store = key if isinstance(key, str) else None
            return RowRef(store, names.get(key) or readable_label(store), "merchant")
    else:
        if level == "group":
            row_of = await _group_level(session, workspace_id)
        else:
            row_of = await _category_level(session, workspace_id, group_id, no_group, all_groups)
        current_flows, previous_flows = await collect_flows(
            session, segments=segments, user_id=user_id, **flow_kwargs
        )
        monthly = await collect_flows(session, segments=month_segments, user_id=user_id, **flow_kwargs)
        current = totals_from([current_flows], kinds)
        previous = totals_from([previous_flows], kinds)
        monthly_totals = [totals_from([seg], kinds) for seg in monthly]
    monthly_by_row = [_spend_by_row(totals, row_of) for totals in monthly_totals]

    totals_by_row = _spend_by_row(current, row_of)
    previous_by_row = _spend_by_row(previous, row_of)
    counts = _count_by_row(current, row_of)
    total = sum(totals_by_row.values(), ZERO)
    previous_total = sum(previous_by_row.values(), ZERO)

    ranked: list[tuple[Decimal, str, dict]] = []
    for ref in counts:
        row_total = totals_by_row.get(ref, ZERO)
        row_previous = previous_by_row.get(ref, ZERO)
        ranked.append(
            (
                row_total,
                ref.label,
                {
                    "id": ref.id,
                    "label": ref.label,
                    "kind": ref.kind,
                    "total": _money(row_total),
                    "share": (
                        round(float(row_total / total), 3) if total > 0 and row_total >= 0 else None
                    ),
                    "previous_total": _money(row_previous),
                    "change": (
                        round(float((row_total - row_previous) / row_previous), 3)
                        if row_previous != 0
                        else None
                    ),
                    "count": counts[ref],
                    "sparkline": [_money(month.get(ref, ZERO)) for month in monthly_by_row],
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))
    rows = [item[2] for item in ranked]
    if level == "category" and all_groups:
        labels = {ref.id: ref.group_label for ref in counts}
        for row in rows:
            row["group_label"] = labels.get(row["id"])
    if level == "merchant" and all_categories:
        for row in rows:
            main = main_category.get(row["id"])
            row["category_id"] = str(main) if main is not None else None
            row["category_label"] = category_names.get(main, NO_CATEGORY_LABEL)

    return {
        "period": {
            "from": period.from_.isoformat(),
            "to": period.to.isoformat(),
            "partial": period.partial,
            "previous_from": period.previous_from.isoformat(),
            "previous_to": period.previous_to.isoformat(),
        },
        "currency": primary_currency,
        "total": _money(total),
        "previous_total": _money(previous_total),
        "rows": rows,
        "unconverted_count": current.unconverted_count,
    }
