"""The Hábitos tab: where, when and in what size the money goes.

Rows are the debits that count as spending under the shared account rules
(`recurring.load_debits`: refund and income categories stay out, so a refund
never shows up as a purchase). A purchase counts at its full value, split
shares are not applied: a habit is what the card charged.
"""
import uuid
from collections import Counter
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.services.categorization.merchant_key import HASHED_PERSON_PREFIX, UNKNOWN_KEY
from app.services.reports.breakdown import NO_CATEGORY_LABEL, _store_names
from app.services.reports.merchant_labels import readable_label
from app.services.reports.periods import Period
from app.services.reports.recurring import load_debits
from app.services.reports.rules import CENT, ZERO

TOP_MERCHANTS = 40
WEEKDAY_LABELS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
#: Lower edge of each purchase-size band (the last one has no upper edge).
BAND_EDGES = (Decimal("0"), Decimal("20"), Decimal("50"), Decimal("200"), Decimal("1000"))
BAND_LABELS = ("até R$ 20", "R$ 20 a R$ 50", "R$ 50 a R$ 200", "R$ 200 a R$ 1.000", "acima de R$ 1.000")


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _merchant_id(key: Optional[str]) -> str:
    """Hashed Pix people and nameless rows each fold into one line."""
    if not key or key == UNKNOWN_KEY:
        return UNKNOWN_KEY
    if key.startswith(HASHED_PERSON_PREFIX):
        return HASHED_PERSON_PREFIX
    return key


def _top_category(entry: dict[str, Any]) -> Optional[str]:
    """Most common category id of a store (None: uncategorized)."""
    top = entry["categories"].most_common(1)[0][0]
    return str(top) if top else None


def _band_of(value: Decimal) -> int:
    index = 0
    for i, edge in enumerate(BAND_EDGES):
        if value >= edge:
            index = i
    return index


async def habits(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    primary_currency: str,
    period: Period,
) -> dict:
    rows = await load_debits(
        session,
        workspace_id=workspace_id,
        primary_currency=primary_currency,
        start=period.from_,
        end=period.to,
    )

    per_merchant: dict[str, dict[str, Any]] = {}
    weekdays: list[dict[str, Any]] = [{"total": ZERO, "count": 0} for _ in range(7)]
    bands: list[dict[str, Any]] = [{"total": ZERO, "count": 0} for _ in BAND_EDGES]
    per_day: dict = {}
    for key, category_id, day, value, _ in rows:
        entry = per_merchant.setdefault(
            _merchant_id(key), {"total": ZERO, "count": 0, "categories": Counter()}
        )
        entry["total"] += value
        entry["count"] += 1
        entry["categories"][category_id] += 1
        slot = weekdays[day.weekday()]
        slot["total"] += value
        slot["count"] += 1
        band = bands[_band_of(value)]
        band["total"] += value
        band["count"] += 1
        per_day[day] = per_day.get(day, ZERO) + value

    top = sorted(per_merchant.items(), key=lambda kv: (-kv[1]["total"], kv[0]))[:TOP_MERCHANTS]
    names = await _store_names(session, workspace_id, {mid for mid, _ in top})
    category_rows = await session.execute(
        select(Category.id, Category.name).where(Category.workspace_id == workspace_id)
    )
    categories = {cid: name for cid, name in category_rows.all()}
    merchants = [
        {
            "id": mid,
            "category_id": _top_category(entry),
            "label": names.get(mid) or readable_label(mid),
            "count": entry["count"],
            "per_month": round(entry["count"] / period.months, 1),
            "avg_ticket": _money(entry["total"] / entry["count"]),
            "total": _money(entry["total"]),
            "category": categories.get(entry["categories"].most_common(1)[0][0], NO_CATEGORY_LABEL),
        }
        for mid, entry in top
    ]

    return {
        "period": {
            "from": period.from_.isoformat(),
            "to": period.to.isoformat(),
            "partial": period.partial,
            "previous_from": period.previous_from.isoformat(),
            "previous_to": period.previous_to.isoformat(),
        },
        "merchants": merchants,
        "weekdays": [
            {
                "weekday": i,
                "label": WEEKDAY_LABELS[i],
                "total": _money(slot["total"]),
                "count": slot["count"],
            }
            for i, slot in enumerate(weekdays)
        ],
        "bands": [
            {
                "label": BAND_LABELS[i],
                "min": str(BAND_EDGES[i]),
                "max": str(BAND_EDGES[i + 1]) if i + 1 < len(BAND_EDGES) else None,
                "count": band["count"],
                "total": _money(band["total"]),
            }
            for i, band in enumerate(bands)
        ],
        "days": [
            {"date": day.isoformat(), "total": _money(total)}
            for day, total in sorted(per_day.items())
            if total > 0
        ],
    }
