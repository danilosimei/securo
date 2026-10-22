"""Highlights of the Visão geral tab: what is out of the ordinary.

A closed list of rules (`RULES`), each with its limits as constants below and
each firing at most once. Every card carries what the screen needs to tell
the story without a second request:

- `tone`: `alert` (worth a look), `opportunity` (money that can be saved, or
  good news) or `info`;
- `sparkline`: the 12 months of the thing the card is about (a group, a
  category, a store or the whole spending), oldest first, or null;
- `drivers`: up to 3 `{label, value, link}` that explain the number (the
  groups that pulled a month up, the stores of a category, a charge before
  and now);
- `actions`: what to do next, the first one is the main one.

`month_pace` is about the current month whatever the period: it compares the
spending so far with a normal month and comes first when it fires. Then at
most `MAX_HIGHLIGHTS` cards, biggest impact first; the review card is never
the one cut, and `frequent_spot` (the habits) always gets a place: it takes
the place of the lowest impact card.
"""
import json
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.categorization import CategorizationSuggestion
from app.models.category import Category
from app.services.reports.breakdown import RowRef, _spend_by_row
from app.services.reports.periods import Period, _month_end, _shift_months
from app.services.reports.rules import CENT, KIND_NORMAL, ZERO, RangeTotals

RULES = (
    "month_pace",
    "spike",
    "what_changed",
    "recurring_changed",
    "recurring_expensive",
    "small_purchases",
    "heavy_group",
    "frequent_spot",
    "to_review",
)
TONES = ("alert", "opportunity", "info")
ACTION_KINDS = ("explore", "review", "recurring", "month")

#: Cards besides the month's pace.
MAX_HIGHLIGHTS = 4
MAX_DRIVERS = 3
SPARKLINE_MONTHS = 12

#: month_pace: from this day of the month on, and only this far from normal.
MONTH_PACE_MIN_DAYS = 5
MONTH_PACE_MIN_GAP = Decimal("0.10")
#: month_pace without closed months in the period: the months right before.
MONTH_PACE_FALLBACK_MONTHS = 3

#: spike: a closed month with spending at least this many times the normal
#: month (the median of the closed months), with a few months to compare.
SPIKE_MULTIPLE = Decimal("1.5")
SPIKE_MIN_CLOSED_MONTHS = 3
#: what_changed: a group's rise over the 3-month average, in money and in share.
WHAT_CHANGED_MIN_DELTA = Decimal("500")
WHAT_CHANGED_MIN_SHARE = Decimal("0.30")
CHANGE_BASELINE_MONTHS = 3
#: recurring_changed: a fixed cost that stopped counts while its last charge
#: is at most this many months before the last closed month.
RECURRING_ENDED_RECENT_MONTHS = 2
#: recurring_expensive: a fixed cost worth this much per year.
RECURRING_EXPENSIVE_MIN_YEAR = Decimal("3000")
#: small_purchases: purchases below this value, adding up to this much per month.
SMALL_PURCHASE_LIMIT = Decimal("50")
SMALL_PURCHASES_MIN_MONTH = Decimal("1000")
#: heavy_group: a group above this share of the period's spending.
HEAVY_GROUP_MIN_SHARE = Decimal("0.05")
#: frequent_spot: the category bought most often, if this often and this much.
FREQUENT_SPOT_MIN_PER_MONTH = Decimal("8")
FREQUENT_SPOT_MIN_MONTH = Decimal("500")
#: Seeded categories that are never a "place": fees, taxes and transfers.
FREQUENT_SPOT_SKIP_SEEDS = ("taxes", "transfers", "investments")

_MONTHS_PT = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
_MONTHS_PT_LONG = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)
_TAXONOMY = Path(__file__).resolve().parents[2] / "data" / "default_categories.json"
REVIEW_PATH = "/categorization"


@lru_cache(maxsize=1)
def _taxonomy() -> dict:
    return json.loads(_TAXONOMY.read_text(encoding="utf-8"))


def _seed_names(entry: dict) -> set[str]:
    names = set(entry["names"].values()) | set(entry.get("aliases") or ())
    return {n.lower() for n in names}


@lru_cache(maxsize=1)
def generic_group_names() -> frozenset[str]:
    """Seeded "Other" group names in every language: too vague to be a highlight."""
    return frozenset(_seed_names(_taxonomy()["groups"]["other"]))


@lru_cache(maxsize=1)
def _not_a_place_names() -> frozenset[str]:
    categories = _taxonomy()["categories"]
    names: set[str] = set()
    for seed in FREQUENT_SPOT_SKIP_SEEDS:
        names |= _seed_names(categories[seed])
    return frozenset(names)


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _month_name(month: date) -> str:
    return f"{_MONTHS_PT[month.month - 1]}/{month.year % 100:02d}"


def _number_pt(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def brl(value: Decimal) -> str:
    """Short money for titles: R$ 786, R$ 4,2 mil, R$ 54 mil, R$ 1,3 mi."""
    amount = abs(value)
    if amount < 1000:
        return f"R$ {int(amount.quantize(Decimal('1')))}"
    if amount < 10_000:
        return f"R$ {_number_pt(amount / 1000, 1)} mil"
    if amount < 1_000_000:
        return f"R$ {int((amount / 1000).quantize(Decimal('1')))} mil"
    return f"R$ {_number_pt(amount / 1_000_000, 1)} mi"


def _percent(value: Decimal) -> str:
    return f"{int((abs(value) * 100).quantize(Decimal('1')))}%"


def _month_index(value: date) -> int:
    return value.year * 12 + value.month - 1


@dataclass
class Change:
    """A group's last closed month against the 3 months before it."""

    ref: RowRef
    target: date
    delta: Decimal
    change: Optional[float]
    average: Decimal


@dataclass
class Context:
    """Everything the rules read, computed once by the overview."""

    session: AsyncSession
    workspace_id: uuid.UUID
    primary_currency: str
    period: Period
    period_name: str
    today: date
    #: Closed months of the period (start, totals).
    closed: list[tuple[date, RangeTotals]]
    totals: RangeTotals
    row_of: Any
    changes: list[Change]
    recurring: list
    #: 12 months ending at the period's last month (start, totals), and the
    #: same for the 12 months ending at the current month.
    spark_months: list[tuple[date, RangeTotals]]
    #: Debits (store, category, day, value, installments) from the first
    #: sparkline month (or the period start) to today.
    debits: list
    #: Spending of the current month so far.
    month_so_far: Decimal


class _Cards:
    def __init__(self, ctx: Context, categories: dict[uuid.UUID, tuple[str, Optional[uuid.UUID], str]]):
        self.ctx = ctx
        self.categories = categories

    # Links ---------------------------------------------------------------

    def link(self, **extra: Optional[str]) -> dict:
        ctx = self.ctx
        return self.explore(ctx.period_name, ctx.period.from_, ctx.period.to, **extra)

    @staticmethod
    def explore(period_name: str, from_: Optional[date], to: Optional[date], **extra: Optional[str]) -> dict:
        link: dict[str, str] = {"tab": "explore", "period": period_name}
        if period_name == "custom" and from_ and to:
            link["from"] = from_.isoformat()
            link["to"] = to.isoformat()
        link.update({k: v for k, v in extra.items() if v is not None})
        return link

    def month_link(self, month: date, **extra: Optional[str]) -> dict:
        return self.explore("custom", month, min(_month_end(month), self.ctx.today), **extra)

    def recurring_link(self) -> dict:
        ctx = self.ctx
        link = self.explore(ctx.period_name, ctx.period.from_, ctx.period.to)
        link["tab"] = "recurring"
        return link

    # Series --------------------------------------------------------------

    def total_series(self) -> list[str]:
        return [_money(t.spending) for _, t in self.ctx.spark_months]

    def group_series(self, ref: RowRef) -> list[str]:
        return [_money(_spend_by_row(t, self.ctx.row_of).get(ref, ZERO)) for _, t in self.ctx.spark_months]

    def category_series(self, category_id: uuid.UUID) -> list[str]:
        return [_money(t.spend_by_category.get(category_id, ZERO)) for _, t in self.ctx.spark_months]

    def store_series(self, keys: set[str], last_month: date) -> list[str]:
        first = _shift_months(last_month, -(SPARKLINE_MONTHS - 1))
        sums = [ZERO] * SPARKLINE_MONTHS
        base = _month_index(first)
        for key, _, day, value, installments in self.ctx.debits:
            if key in keys and installments is None:
                index = _month_index(day) - base
                if 0 <= index < SPARKLINE_MONTHS:
                    sums[index] += value
        return [_money(v) for v in sums]

    def category_name(self, category_id: Optional[uuid.UUID]) -> Optional[str]:
        entry = self.categories.get(category_id) if category_id else None
        return entry[0] if entry else None


def card(
    rule: str,
    title: str,
    value: Decimal | int,
    compare: str,
    *,
    tone: str,
    actions: list[dict],
    sparkline: Optional[list[str]] = None,
    drivers: Optional[list[dict]] = None,
    impact: Optional[Decimal] = None,
) -> dict:
    return {
        "rule": rule,
        "title": title,
        "value": _money(value) if isinstance(value, Decimal) else str(value),
        "compare": compare,
        # The main action's link, as before the actions existed.
        "link": actions[0]["link"] if actions else {},
        "tone": tone,
        "sparkline": sparkline,
        "drivers": (drivers or [])[:MAX_DRIVERS],
        "actions": actions,
        "_impact": impact if impact is not None else (value if isinstance(value, Decimal) else ZERO),
    }


def action(kind: str, label: str, link: dict) -> dict:
    return {"kind": kind, "label": label, "link": link}


def driver(label: str, value: Decimal, link: Optional[dict] = None) -> dict:
    return {"label": label, "value": _money(value), "link": link}


async def category_rows(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[Any, tuple[str, Optional[uuid.UUID], str]]:
    """Category id -> (name, group id, report kind)."""
    rows = await session.execute(
        select(Category.id, Category.name, Category.group_id, Category.report_kind).where(
            Category.workspace_id == workspace_id
        )
    )
    return {cid: (name, gid, kind) for cid, name, gid, kind in rows.all()}


async def build(ctx: Context) -> list[dict]:
    cards = _Cards(ctx, await category_rows(ctx.session, ctx.workspace_id))

    pace = _month_pace(ctx, cards)
    found: list[dict] = []
    for rule in (_spike, _what_changed, _heavy_group):
        item = rule(ctx, cards)
        if item is not None:
            found.append(item)
    for async_rule in (_recurring_changed, _recurring_expensive, _small_purchases, _frequent_spot):
        item = await async_rule(ctx, cards)
        if item is not None:
            found.append(item)
    review = await _to_review(ctx, cards)

    # Biggest impact first; the review card is never the one cut, and the
    # habits card always makes it (it gives up the lowest impact one).
    found.sort(key=lambda c: (-c["_impact"], c["rule"]))
    room = MAX_HIGHLIGHTS - 1 if review is not None else MAX_HIGHLIGHTS
    spot = next((c for c in found if c["rule"] == "frequent_spot"), None)
    if spot is not None and spot not in found[:room]:
        found = found[: room - 1] + [spot]
    chosen = found[:room] + ([review] if review is not None else [])
    out = ([pace] if pace is not None else []) + chosen
    for item in out:
        item.pop("_impact", None)
    return out


def _month_pace(ctx: Context, cards: _Cards) -> Optional[dict]:
    """The current month so far against a normal month at the same day."""
    today = ctx.today
    if today.day < MONTH_PACE_MIN_DAYS or ctx.month_so_far <= 0:
        return None
    pool = [t.spending for _, t in ctx.closed]
    if not pool:
        current = today.replace(day=1)
        pool = [
            t.spending
            for start, t in ctx.spark_months
            if _month_index(current) - MONTH_PACE_FALLBACK_MONTHS <= _month_index(start) < _month_index(current)
        ]
    if not pool:
        return None
    typical = Decimal(median(pool))
    days = monthrange(today.year, today.month)[1]
    expected = typical * today.day / days
    if typical <= 0 or expected <= 0:
        return None
    gap = ctx.month_so_far / expected - 1
    if abs(gap) < MONTH_PACE_MIN_GAP:
        return None
    projection = ctx.month_so_far / today.day * days
    above = gap > 0
    title = (
        f"Em {today.day} dias você gastou {brl(ctx.month_so_far)}, "
        f"{_percent(gap)} {'acima' if above else 'abaixo'} do normal"
    )
    return card(
        "month_pace",
        title,
        ctx.month_so_far,
        f"Nesse passo, o mês fecha em {brl(projection)}",
        tone="alert" if above else "opportunity",
        actions=[action("month", "Ver o mês", cards.explore("this_month", None, None))],
        drivers=[
            # Always these three, in this order: what a normal month has by
            # today, where this month ends at this pace, a whole normal month.
            driver("Normal até hoje", expected),
            driver("Nesse passo, fecha em", projection),
            driver("Um mês normal", typical),
        ],
    )


def _spike(ctx: Context, cards: _Cards) -> Optional[dict]:
    """A closed month well above a normal month."""
    if len(ctx.closed) < SPIKE_MIN_CLOSED_MONTHS:
        return None
    center = Decimal(median([t.spending for _, t in ctx.closed]))
    if center <= 0:
        return None
    month, top = max(ctx.closed, key=lambda mt: mt[1].spending)
    if top.spending < SPIKE_MULTIPLE * center:
        return None
    per_group = [_spend_by_row(t, ctx.row_of) for _, t in ctx.closed]
    this_month = _spend_by_row(top, ctx.row_of)
    diffs = []
    for ref, value in this_month.items():
        usual = Decimal(median([g.get(ref, ZERO) for g in per_group]))
        if value - usual > 0:
            diffs.append((value - usual, ref))
    diffs.sort(key=lambda d: (-d[0], d[1].label))
    drivers = [
        driver(ref.label, diff, cards.month_link(month, **_ref_filter(ref)))
        for diff, ref in diffs[:MAX_DRIVERS]
    ]
    ratio = top.spending / center
    return card(
        "spike",
        f"{_month_name(month)} custou {_number_pt(ratio, 1)} vezes um mês normal",
        top.spending,
        f"Um mês normal: {brl(center)}",
        tone="alert",
        sparkline=cards.total_series(),
        drivers=drivers,
        # Opens the month that spiked, not the whole period.
        actions=[action("month", f"Ver {_month_name(month)}", cards.month_link(month))],
    )


def _ref_filter(ref: RowRef) -> dict[str, Optional[str]]:
    if ref.kind == "category":
        return {"category_id": ref.id}
    return {"group_id": ref.id or "none"}


def _what_changed(ctx: Context, cards: _Cards) -> Optional[dict]:
    """The group with the biggest rise in the last closed month."""
    rises = [
        c
        for c in ctx.changes
        if c.ref.kind == "group"
        and c.ref.id is not None
        and c.delta >= WHAT_CHANGED_MIN_DELTA
        and c.average > 0
        and c.change is not None
        and Decimal(str(c.change)) >= WHAT_CHANGED_MIN_SHARE
    ]
    if not rises:
        return None
    top = max(rises, key=lambda c: c.delta)
    ref = top.ref
    # The categories of the group that rose most in that month.
    starts = [start for start, _ in ctx.spark_months]
    drivers = []
    if top.target in starts:
        at = starts.index(top.target)
        window = ctx.spark_months[max(0, at - CHANGE_BASELINE_MONTHS):at]
        target_spend = ctx.spark_months[at][1].spend_by_category
        rows = []
        for cid, value in target_spend.items():
            if cid is None or cards.categories.get(cid, ("", None, ""))[1] is None:
                continue
            if str(cards.categories[cid][1]) != ref.id:
                continue
            before = sum((t.spend_by_category.get(cid, ZERO) for _, t in window), ZERO)
            delta = value - before / CHANGE_BASELINE_MONTHS
            if delta > 0:
                rows.append((delta, cards.categories[cid][0], cid))
        rows.sort(key=lambda r: (-r[0], r[1]))
        drivers = [
            driver(name, delta, cards.month_link(top.target, group_id=ref.id, category_id=str(cid)))
            for delta, name, cid in rows[:MAX_DRIVERS]
        ]
    return card(
        "what_changed",
        f"{ref.label} subiu {brl(top.delta)} em {_month_name(top.target)}",
        top.delta,
        f"+{round((top.change or 0) * 100)}% sobre a média dos 3 meses antes",
        tone="alert",
        sparkline=cards.group_series(ref),
        drivers=drivers,
        actions=[
            action("explore", f"Ver {ref.label}", cards.link(group_id=ref.id)),
            action("month", f"Ver {_month_name(top.target)}", cards.month_link(top.target, group_id=ref.id)),
        ],
    )


def _heavy_group(ctx: Context, cards: _Cards) -> Optional[dict]:
    """The group that costs most per year, among those above the share."""
    total = ctx.totals.spending
    if total <= 0:
        return None
    by_group = _spend_by_row(ctx.totals, ctx.row_of)
    heavy = [
        (value, ref)
        for ref, value in by_group.items()
        if ref.kind == "group"
        and ref.id is not None
        and ref.label.lower() not in generic_group_names()
        and value / total > HEAVY_GROUP_MIN_SHARE
    ]
    if not heavy:
        return None
    value, ref = max(heavy, key=lambda h: (h[0], h[1].label))
    yearly = value / ctx.period.months * 12
    inside = sorted(
        (
            (spend, cards.categories[cid][0], cid)
            for cid, spend in ctx.totals.spend_by_category.items()
            if cid is not None and cid in cards.categories and str(cards.categories[cid][1]) == ref.id
            and spend > 0
        ),
        key=lambda r: (-r[0], r[1]),
    )
    return card(
        "heavy_group",
        f"{ref.label} custa {brl(yearly)} por ano",
        yearly,
        f"{_percent(value / total)} de tudo que você gastou",
        tone="info",
        sparkline=cards.group_series(ref),
        drivers=[
            driver(name, spend, cards.link(group_id=ref.id, category_id=str(cid)))
            for spend, name, cid in inside[:MAX_DRIVERS]
        ],
        actions=[action("explore", f"Ver {ref.label}", cards.link(group_id=ref.id))],
    )


async def _frequent_spot(ctx: Context, cards: _Cards) -> Optional[dict]:
    """The category bought most often: a habit worth knowing about."""
    period = ctx.period
    months = Decimal(period.months)
    count: dict[uuid.UUID, int] = {}
    per_store: dict[uuid.UUID, dict[str, int]] = {}
    for key, cid, day, _, _ in ctx.debits:
        if cid is None or not (period.from_ <= day <= period.to):
            continue
        name, _, kind = cards.categories.get(cid, ("", None, KIND_NORMAL))
        if kind != KIND_NORMAL or name.lower() in _not_a_place_names():
            continue
        count[cid] = count.get(cid, 0) + 1
        if key:
            stores = per_store.setdefault(cid, {})
            stores[key] = stores.get(key, 0) + 1
    candidates = [
        (n, cid)
        for cid, n in count.items()
        if Decimal(n) / months >= FREQUENT_SPOT_MIN_PER_MONTH
        and ctx.totals.spend_by_category.get(cid, ZERO) / months >= FREQUENT_SPOT_MIN_MONTH
    ]
    if not candidates:
        return None
    n, cid = max(candidates, key=lambda c: (c[0], cards.categories[c[1]][0]))
    name = cards.categories[cid][0]
    per_month = ctx.totals.spend_by_category[cid] / months
    times = int((Decimal(n) / months).quantize(Decimal("1")))
    stores = sorted(per_store.get(cid, {}).items(), key=lambda s: (-s[1], s[0]))[:MAX_DRIVERS]
    labels = await store_labels(ctx.session, ctx.workspace_id, [key for key, _ in stores])
    drivers = []
    for key, visits in stores:
        spent = sum(
            (v for k, c, d, v, _ in ctx.debits if k == key and c == cid and period.from_ <= d <= period.to),
            ZERO,
        )
        drivers.append(
            driver(
                f"{labels.get(key, key)} ({visits}×)",
                spent,
                cards.link(category_id=str(cid), merchant=key),
            )
        )
    return card(
        "frequent_spot",
        f"{name}: {brl(per_month)} por mês, cerca de {times} vezes por mês",
        per_month,
        f"{n} compras no período",
        tone="info",
        sparkline=cards.category_series(cid),
        drivers=drivers,
        actions=[action("explore", "Ver no Explorar", cards.link(category_id=str(cid)))],
        impact=per_month,
    )




def _fixed_name(cards: _Cards, item: Any, names: dict[str, str]) -> str:
    """A fixed cost as people know it: its category and its store
    ("Financiamento do veículo (Stellantis)"), or the store alone when the
    category says nothing."""
    store = names.get(item.merchant_key, item.merchant_key)
    category = cards.category_name(item.category_id)
    vague = {"sem categoria"} | set(generic_group_names())
    if category and category.lower() not in vague and category.lower() != store.lower():
        return f"{category} ({store})"
    return store


_CHANGE_NOTES = {"new": ("começou", "começaram"), "up": ("subiu", "subiram"), "ended": ("acabou", "acabaram")}


async def _recurring_changed(ctx: Context, cards: _Cards) -> Optional[dict]:
    """Fixed costs that started, went up or ended (recently)."""
    last_closed = _month_index(ctx.today) - 1
    moving: list[tuple[Any, Decimal, str]] = []
    for r in ctx.recurring:
        if r.status == "new":
            moving.append((r, r.amount_month, "new"))
        elif r.status == "up" and r.previous_amount is not None and r.last_amount is not None:
            moving.append((r, r.last_amount - r.previous_amount, "up"))
        elif r.status == "stopped" and (
            r.ended_on is not None
            or _month_index(r.last_date) >= last_closed - RECURRING_ENDED_RECENT_MONTHS
        ):
            moving.append((r, -r.amount_month, "ended"))
    if not moving:
        return None
    moving.sort(key=lambda m: (-abs(m[1]), m[0].merchant_key))
    names = await store_labels(ctx.session, ctx.workspace_id, [r.merchant_key for r, _, _ in moving])
    net = sum((delta for _, delta, _ in moving), ZERO)
    ended = [m for m in moving if m[2] == "ended"]

    def store_link(r: Any) -> dict:
        return cards.link(
            merchant=r.merchant_key,
            category_id=str(r.category_id) if r.category_id else None,
        )

    if len(moving) == 1:
        r, delta, kind = moving[0]
        before = {"new": ZERO, "up": r.previous_amount, "ended": r.amount_month}[kind]
        now = {"new": r.amount_month, "up": r.last_amount, "ended": ZERO}[kind]
        drivers = [driver("Antes", before or ZERO), driver("Agora", now or ZERO)]
    else:
        drivers = [
            driver(
                f"{names.get(r.merchant_key, r.merchant_key)} ({_CHANGE_NOTES[kind][0]})",
                delta,
                store_link(r),
            )
            for r, delta, kind in moving
        ]

    if ended and net < 0:
        tone = "opportunity"
        if len(moving) == 1:
            r = ended[0][0]
            since = _MONTHS_PT_LONG[_shift_months(r.last_date.replace(day=1), 1).month - 1]
            title = (
                f"{_fixed_name(cards, r, names)} acabou: "
                f"{brl(r.amount_month)} por mês a menos desde {since}"
            )
        else:
            title = f"Seus gastos fixos caíram {brl(-net)} por mês"
        compare = f"{brl(-net * 12)} por ano a menos"
    else:
        tone = "alert"
        r, delta, kind = moving[0]
        name = names.get(r.merchant_key, r.merchant_key)
        if len(moving) == 1 and kind == "new":
            title = f"{name} é um gasto fixo novo: {brl(r.amount_month)} por mês"
        elif len(moving) == 1:
            title = f"{name} subiu para {brl(r.last_amount or r.amount_month)} por mês"
        elif ended:
            title = f"Seus gastos fixos subiram {brl(net)} por mês"
        else:
            title = f"{len(moving)} gastos fixos começaram ou subiram: {brl(net)} a mais por mês"
        compare = f"{brl(net * 12)} por ano a mais"
    if len(moving) > 1:
        counts = []
        for kind in ("ended", "new", "up"):
            n = sum(1 for m in moving if m[2] == kind)
            if n:
                counts.append(f"{n} {_CHANGE_NOTES[kind][0 if n == 1 else 1]}")
        compare = ", ".join(counts)

    actions = [action("recurring", "Ver gastos fixos", cards.recurring_link())]
    if len(moving) == 1:
        actions.append(action("explore", "Ver as cobranças", store_link(moving[0][0])))
    return card(
        "recurring_changed",
        title,
        abs(net),
        compare,
        tone=tone,
        sparkline=cards.store_series({r.merchant_key for r, _, _ in moving}, ctx.today.replace(day=1)),
        drivers=drivers,
        actions=actions,
        impact=abs(net) * 12,
    )


async def _recurring_expensive(ctx: Context, cards: _Cards) -> Optional[dict]:
    """The fixed cost that weighs most in a year."""
    active = [r for r in ctx.recurring if r.status != "stopped"]
    if not active or active[0].amount_year < RECURRING_EXPENSIVE_MIN_YEAR:
        return None
    top = active[0]
    names = await store_labels(ctx.session, ctx.workspace_id, [top.merchant_key])
    drivers = []
    if top.previous_amount is not None and top.last_amount is not None:
        drivers = [driver("Antes", top.previous_amount), driver("Agora", top.last_amount)]
    store_link = cards.link(
        merchant=top.merchant_key,
        category_id=str(top.category_id) if top.category_id else None,
    )
    return card(
        "recurring_expensive",
        f"{names[top.merchant_key]} custa {brl(top.amount_year)} por ano",
        top.amount_year,
        f"{brl(top.amount_month)} por mês",
        tone="opportunity",
        sparkline=cards.store_series({top.merchant_key}, _shift_months(ctx.today.replace(day=1), -1)),
        drivers=drivers,
        actions=[
            action("explore", "Ver as cobranças", store_link),
            action("recurring", "Ver gastos fixos", cards.recurring_link()),
        ],
    )


async def _small_purchases(ctx: Context, cards: _Cards) -> Optional[dict]:
    """Many small debits that add up."""
    period = ctx.period
    small = [
        (key, cid, day, value)
        for key, cid, day, value, _ in ctx.debits
        if value < SMALL_PURCHASE_LIMIT and period.from_ <= day <= period.to
    ]
    per_month = sum((v for *_, v in small), ZERO) / period.months
    if per_month < SMALL_PURCHASES_MIN_MONTH:
        return None
    by_store: dict[str, list] = {}
    for key, cid, _, value in small:
        if key:
            by_store.setdefault(key, []).append((cid, value))
    top = sorted(by_store.items(), key=lambda s: (-sum((v for _, v in s[1]), ZERO), s[0]))[:MAX_DRIVERS]
    names = await store_labels(ctx.session, ctx.workspace_id, [key for key, _ in top])
    drivers = []
    for key, items in top:
        cids = [cid for cid, _ in items]
        main = max(set(cids), key=lambda c: (cids.count(c), str(c)))
        drivers.append(
            driver(
                f"{names[key]} ({len(items)}×)",
                sum((v for _, v in items), ZERO),
                cards.link(merchant=key, category_id=str(main) if main else None),
            )
        )
    first = ctx.spark_months[0][0]
    sums = [ZERO] * len(ctx.spark_months)
    for _, _, day, value, _ in ctx.debits:
        index = _month_index(day) - _month_index(first)
        if value < SMALL_PURCHASE_LIMIT and 0 <= index < len(sums):
            sums[index] += value
    return card(
        "small_purchases",
        f"Compras abaixo de {brl(SMALL_PURCHASE_LIMIT)} somam {brl(per_month)} por mês",
        per_month,
        f"{len(small)} compras, {brl(per_month * 12)} por ano",
        tone="opportunity",
        sparkline=[_money(v) for v in sums],
        drivers=drivers,
        actions=[action("explore", "Ver as lojas", cards.link(view="merchant"))],
    )


async def _to_review(ctx: Context, cards: _Cards) -> Optional[dict]:
    """Things waiting for a look, in a single card."""
    pending = (
        await ctx.session.execute(
            select(func.count(CategorizationSuggestion.id)).where(
                CategorizationSuggestion.workspace_id == ctx.workspace_id,
                CategorizationSuggestion.status == "pending",
            )
        )
    ).scalar_one()
    parts = []
    count = 0
    if pending:
        count += pending
        parts.append(f"{pending} {'sugestões' if pending > 1 else 'sugestão'} na Revisar")
    if ctx.totals.uncategorized_income > 0:
        count += 1
        parts.append(f"{brl(ctx.totals.uncategorized_income)} de renda sem categoria")
    odd = [r for r in ctx.recurring if r.atypical_amount is not None]
    if odd:
        names = await store_labels(ctx.session, ctx.workspace_id, [r.merchant_key for r in odd])
        for r in odd:
            count += 1
            parts.append(f"{names[r.merchant_key]} cobrou {brl(r.atypical_amount or ZERO)}, fora do normal")
    if not parts:
        return None
    return card(
        "to_review",
        f"{count} {'item' if count == 1 else 'itens'} para revisar",
        count,
        " · ".join(parts),
        tone="info",
        actions=[action("review", "Revisar", {"path": REVIEW_PATH})],
    )


async def store_labels(session: AsyncSession, workspace_id: uuid.UUID, keys: list[str]) -> dict[str, str]:
    from app.services.reports.breakdown import _store_names
    from app.services.reports.merchant_labels import readable_label

    names = await _store_names(session, workspace_id, set(keys))
    return {key: names.get(key) or readable_label(key) for key in keys}
