"""Corrections of the model: what reaches the prompt as counter-examples."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.categorization import CategorizationEvent, CategorizationSuggestion
from app.models.transaction import Transaction
from app.services.categorization.corrections import load_corrections, recent_corrections
from app.services.categorization.prompts import build_category_index

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _tx(session, user, account, description):
    tx = Transaction(
        id=uuid.uuid4(), user_id=user.id, workspace_id=account.workspace_id,
        account_id=account.id, description=description, amount=Decimal("10.00"),
        date=date(2026, 9, 10), effective_date=date(2026, 9, 10), type="debit",
        source="sync", status="posted", category_source="user",
    )
    session.add(tx)
    return tx


def _event(session, workspace_id, tx, *, previous, new, days_ago=0, undone=False,
           source="user", previous_source="ai"):
    session.add(CategorizationEvent(
        id=uuid.uuid4(), workspace_id=workspace_id, transaction_id=tx.id, source=source,
        previous_source=previous_source, previous_category_id=previous, new_category_id=new,
        created_at=NOW - timedelta(days=days_ago),
        undone_at=NOW if undone else None,
    ))


@pytest.mark.asyncio
async def test_person_moving_an_ai_category_is_a_correction(
    session, test_user, test_workspace, test_account, test_categories
):
    a, b = test_categories[0].id, test_categories[1].id
    moved = _tx(session, test_user, test_account, "CINEMARK 33")
    same = _tx(session, test_user, test_account, "NETFLIX")
    undone = _tx(session, test_user, test_account, "UBER *TRIP")
    from_rule = _tx(session, test_user, test_account, "PADARIA ESTRELA")
    await session.flush()
    _event(session, test_workspace.id, moved, previous=a, new=b)
    _event(session, test_workspace.id, same, previous=a, new=a)
    _event(session, test_workspace.id, undone, previous=a, new=b, undone=True)
    _event(session, test_workspace.id, from_rule, previous=a, new=b, previous_source="rule")
    await session.commit()

    assert await recent_corrections(session, test_workspace.id) == [("CINEMARK", b, a)]
    index = build_category_index(test_categories, [])
    text = await load_corrections(session, test_workspace.id, index)
    assert text == f"CINEMARK -> {index.to_short(b)} (não {index.to_short(a)})"


@pytest.mark.asyncio
async def test_most_recent_first_one_per_store_and_cut_by_date(
    session, test_user, test_workspace, test_account, test_categories
):
    a, b, c = (cat.id for cat in test_categories[:3])
    old = _tx(session, test_user, test_account, "CINEMARK 33")
    new = _tx(session, test_user, test_account, "CINEMARK 44")
    other = _tx(session, test_user, test_account, "NETFLIX")
    await session.flush()
    _event(session, test_workspace.id, old, previous=a, new=b, days_ago=10)
    _event(session, test_workspace.id, new, previous=a, new=c, days_ago=2)
    _event(session, test_workspace.id, other, previous=b, new=a, days_ago=5)
    await session.commit()

    assert await recent_corrections(session, test_workspace.id) == [
        ("CINEMARK", c, a), ("NETFLIX", a, b),
    ]
    # Only what happened before the cut: the newer CINEMARK choice is not there yet.
    assert await recent_corrections(
        session, test_workspace.id, before=NOW - timedelta(days=4)
    ) == [("NETFLIX", a, b), ("CINEMARK", b, a)]


@pytest.mark.asyncio
async def test_accepted_suggestion_with_another_final_choice_is_a_correction(
    session, test_user, test_workspace, test_account, test_categories
):
    a, b = test_categories[0].id, test_categories[1].id
    differs = _tx(session, test_user, test_account, "CINEMARK 33")
    agrees = _tx(session, test_user, test_account, "NETFLIX")
    await session.flush()
    for tx, suggested in ((differs, a), (agrees, b)):
        session.add(CategorizationSuggestion(
            id=uuid.uuid4(), workspace_id=test_workspace.id, transaction_id=tx.id,
            merchant_key=tx.merchant_key or "X", category_id=suggested, status="accepted",
            created_at=NOW - timedelta(days=3), resolved_at=NOW - timedelta(days=1),
        ))
        _event(session, test_workspace.id, tx, previous=None, new=b, days_ago=1,
               previous_source="none")
    await session.commit()

    assert await recent_corrections(session, test_workspace.id) == [("CINEMARK", b, a)]
