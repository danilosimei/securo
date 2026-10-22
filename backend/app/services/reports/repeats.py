""""O que se repete" on the Visão geral tab: where the person goes back to,
several times a month. It is about behaviour, which nobody sees from one
purchase to the next: the stores bought from at least `MIN_TIMES` times in
the period, in at least `MIN_MONTHS` different months and, in the months
they showed up, at least `MIN_PER_ACTIVE_MONTH` times a month and at least
`MIN_IN_A_MONTH` times in some single month (a habit, not a rare big purchase
or a bill), most purchases first (biggest spending per month breaks a tie),
and the categories bought from most often.

Left out on purpose: bills and subscriptions (fixed costs, already in Fixos),
fees and taxes the store key groups (IOF, broker fees...), seeded tax and
transfer categories, and payments to people (`PIX:` keys).
"""
import uuid
from decimal import Decimal
from typing import Any, Optional

from app.services.categorization.merchant_key import PERSON_PREFIX, UNKNOWN_KEY
from app.services.reports.highlights import (
    Context,
    _money,
    _month_index,
    _not_a_place_names,
    store_labels,
)
from app.services.reports.merchant_labels import FIXED_LABELS
from app.services.reports.rules import KIND_NORMAL, ZERO

MIN_TIMES = 3
MIN_MONTHS = 3
#: Purchases per month, counting only the months the store appeared in.
MIN_PER_ACTIVE_MONTH = Decimal("1.5")
#: Purchases in the busiest month: a bill (1-2 a month) never gets here.
MIN_IN_A_MONTH = 3
MAX_STORES = 8
MAX_CATEGORIES = 3
NO_CATEGORY_NAME = "Sem categoria"


def _skip_store(key: Optional[str], fixed: set[str]) -> bool:
    return (
        not key
        or key == UNKNOWN_KEY
        or key.startswith(PERSON_PREFIX)
        or key in FIXED_LABELS
        or key in fixed
    )


async def build(ctx: Context, categories: dict[Any, tuple[str, Optional[uuid.UUID], str]]) -> dict:
    period = ctx.period
    months = Decimal(period.months)
    fixed = {r.merchant_key for r in ctx.recurring if r.status != "stopped"}
    skip_names = _not_a_place_names()

    def counts(category_id: Optional[uuid.UUID]) -> bool:
        if category_id is None:
            return True
        name, _, kind = categories.get(category_id, ("", None, KIND_NORMAL))
        return kind == KIND_NORMAL and name.lower() not in skip_names

    stores: dict[str, dict] = {}
    by_category: dict[uuid.UUID, list] = {}
    for key, cid, day, value, _ in ctx.debits:
        if not (period.from_ <= day <= period.to) or not counts(cid):
            continue
        if cid is not None and key and not key.startswith(PERSON_PREFIX) and key not in FIXED_LABELS:
            entry = by_category.setdefault(cid, [0, ZERO])
            entry[0] += 1
            entry[1] += value
        if _skip_store(key, fixed):
            continue
        store = stores.setdefault(key, {"times": 0, "total": ZERO, "months": {}, "cats": {}})
        store["times"] += 1
        store["total"] += value
        month = _month_index(day)
        store["months"][month] = store["months"].get(month, 0) + 1
        store["cats"][cid] = store["cats"].get(cid, 0) + 1

    chosen = [
        (key, s)
        for key, s in stores.items()
        if s["times"] >= MIN_TIMES
        and len(s["months"]) >= MIN_MONTHS
        and Decimal(s["times"]) / len(s["months"]) >= MIN_PER_ACTIVE_MONTH
        and max(s["months"].values()) >= MIN_IN_A_MONTH
    ]
    chosen.sort(key=lambda item: (-item[1]["times"], -item[1]["total"], item[0]))
    chosen = chosen[:MAX_STORES]
    names = await store_labels(ctx.session, ctx.workspace_id, [key for key, _ in chosen])

    first = _month_index(ctx.spark_months[0][0])
    repeats = []
    for key, s in chosen:
        cid = max(s["cats"], key=lambda c: (s["cats"][c], str(c)))
        name, group_id, _ = categories.get(cid, (NO_CATEGORY_NAME, None, KIND_NORMAL)) if cid else (NO_CATEGORY_NAME, None, KIND_NORMAL)
        sparkline = [ZERO] * len(ctx.spark_months)
        for k, _, day, value, _ in ctx.debits:
            index = _month_index(day) - first
            if k == key and 0 <= index < len(sparkline):
                sparkline[index] += value
        month_total = s["total"] / months
        repeats.append(
            {
                "merchant": key,
                "label": names[key],
                "category_id": str(cid) if cid else None,
                "category": name,
                "group_id": str(group_id) if group_id else None,
                "times": s["times"],
                "months": len(s["months"]),
                "per_month": round(float(Decimal(s["times"]) / months), 1),
                "month_total": _money(month_total),
                "year_total": _money(month_total * 12),
                "avg_purchase": _money(s["total"] / s["times"]),
                "sparkline": [_money(v) for v in sparkline],
            }
        )

    top_categories = sorted(
        ((n, total, cid) for cid, (n, total) in by_category.items() if n >= MIN_TIMES),
        key=lambda c: (-c[0], -c[1], str(c[2])),
    )[:MAX_CATEGORIES]
    repeat_categories = [
        {
            "category_id": str(cid),
            "category": categories[cid][0],
            "group_id": str(categories[cid][1]) if categories[cid][1] else None,
            "times": n,
            "per_month": round(float(Decimal(n) / months), 1),
            "month_total": _money(total / months),
        }
        for n, total, cid in top_categories
        if cid in categories
    ]
    return {"repeats": repeats, "repeat_categories": repeat_categories}
