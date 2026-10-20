import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.categorization import (
    CategorizationEvent,
    CategorizationRun,
    CategorizationSettings,
    CategorizationSuggestion,
)
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.schemas.categorization import CategorizationSettingsUpdate
from app.services.categorization import pipeline
from app.services.categorization.memory import learn_choice, remember_ai_decision
from app.services.categorization.pipeline import (
    Candidate,
    collect_candidates,
    group_by_key,
    memory_pass,
    run_categorization,
    suggest_without_ai,
)
from app.services.categorization.settings import get_settings, update_settings


def _tx(
    session, user, account, description, *,
    source="none", category_id=None, amount="10.00", day=date(2026, 9, 10),
    status="posted", created_at=None, **extra,
):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=description,
        amount=Decimal(amount),
        date=day,
        effective_date=day,
        type=extra.pop("type", "debit"),
        source=extra.pop("tx_source", "sync"),
        status=status,
        category_id=category_id,
        category_source=source,
        created_at=created_at or datetime.now(timezone.utc),
        **extra,
    )
    session.add(tx)
    return tx


def _cand(description, amount, day, *, tx_type="debit", account_type="credit_card"):
    from app.services.categorization.merchant_key import merchant_key

    return Candidate(
        tx_id=uuid.uuid4(),
        key=merchant_key(description),
        description=description,
        payee_name=None,
        amount=Decimal(amount),
        type=tx_type,
        account_type=account_type,
        date=day,
        current_category_id=None,
    )


async def _events(session):
    return list((await session.execute(select(CategorizationEvent))).scalars().all())


async def _suggestions(session):
    return list((await session.execute(select(CategorizationSuggestion))).scalars().all())


@pytest.mark.asyncio
async def test_collect_candidates_includes_none_and_provider_excludes_user(
    session, test_user, test_workspace, test_account, test_categories
):
    cat = test_categories[0].id
    none_tx = _tx(session, test_user, test_account, "PADARIA PAO QUENTE")
    provider_tx = _tx(session, test_user, test_account, "UBER *TRIP", source="provider", category_id=cat)
    _tx(session, test_user, test_account, "MERCADO", source="user", category_id=cat)
    _tx(session, test_user, test_account, "FARMACIA", source="legacy", category_id=cat)
    _tx(session, test_user, test_account, "REGRA", source="rule", category_id=cat)
    _tx(session, test_user, test_account, "PENDENTE", status="pending")
    _tx(session, test_user, test_account, "TRANSF", transfer_pair_id=uuid.uuid4())
    _tx(session, test_user, test_account, "IGNORADA", is_ignored=True)
    _tx(session, test_user, test_account, "SALDO INICIAL", tx_source="opening_balance")
    await session.commit()

    cands = await collect_candidates(session, test_workspace.id)

    by_id = {c.tx_id: c for c in cands}
    assert set(by_id) == {none_tx.id, provider_tx.id}
    assert by_id[none_tx.id].key == "PADARIA PAO QUENTE"
    assert by_id[none_tx.id].account_type == "checking"
    assert by_id[provider_tx.id].current_category_id == cat
    assert by_id[provider_tx.id].key == "UBER"


@pytest.mark.asyncio
async def test_collect_candidates_uses_payee_name_for_key(
    session, test_user, test_workspace, test_account
):
    from app.models.payee import Payee

    payee = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Amazon Servicos de Varejo", source="sync",
    )
    session.add(payee)
    await session.flush()
    _tx(session, test_user, test_account, "AMAZON PSJ AMZ", payee_id=payee.id)
    await session.commit()

    [cand] = await collect_candidates(session, test_workspace.id)
    assert cand.payee_name == "Amazon Servicos de Varejo"
    assert cand.key.startswith("P:")


@pytest.mark.asyncio
async def test_collect_candidates_skips_pending_and_dismissed_until_new_purchase(
    session, test_user, test_workspace, test_account
):
    now = datetime.now(timezone.utc)
    old = _tx(session, test_user, test_account, "LOJA X", created_at=now - timedelta(days=3))
    pending = _tx(session, test_user, test_account, "LOJA Y")
    await session.flush()
    session.add_all([
        CategorizationSuggestion(
            id=uuid.uuid4(), workspace_id=test_workspace.id, transaction_id=old.id,
            merchant_key="LOJA X", status="dismissed",
            created_at=now - timedelta(days=2), resolved_at=now - timedelta(days=1),
        ),
        CategorizationSuggestion(
            id=uuid.uuid4(), workspace_id=test_workspace.id, transaction_id=pending.id,
            merchant_key="LOJA Y", status="pending",
        ),
    ])
    sibling_before = _tx(session, test_user, test_account, "LOJA X", created_at=now - timedelta(hours=30))
    new_purchase = _tx(session, test_user, test_account, "LOJA X", created_at=now)
    await session.commit()

    ids = {c.tx_id for c in await collect_candidates(session, test_workspace.id)}

    assert ids == {new_purchase.id}
    assert old.id not in ids and sibling_before.id not in ids and pending.id not in ids


def test_group_by_key_merges_ifood_variants_and_detects_recurring():
    cands = [
        _cand("IFOOD *REST 8812", "30.00", date(2026, 6, 3)),
        _cand("IFOOD*CLUB 4410", "55.00", date(2026, 7, 9)),
        _cand("IFOOD *REST 8812", "80.00", date(2026, 8, 1)),
        _cand("NETFLIX", "39.90", date(2026, 6, 5)),
        _cand("NETFLIX", "39.90", date(2026, 7, 5)),
        _cand("NETFLIX", "41.00", date(2026, 8, 5)),
        _cand("NETFLIX", "39.90", date(2026, 8, 20)),
    ]

    groups = group_by_key(cands)

    assert set(groups) == {"IFOOD", "NETFLIX"}
    ifood = groups["IFOOD"]
    assert ifood.count == 3
    assert len(ifood.tx_ids) == 3
    assert ifood.samples == ["IFOOD *REST 8812", "IFOOD*CLUB 4410"]
    assert ifood.median_amount == Decimal("55.00")
    assert ifood.type == "debit"
    assert ifood.account_type == "credit_card"
    assert ifood.recurring is False
    netflix = groups["NETFLIX"]
    assert netflix.samples == ["NETFLIX"]
    assert netflix.recurring is True


def test_group_by_key_two_months_is_not_recurring():
    cands = [
        _cand("SPOTIFY", "21.90", date(2026, 6, 5)),
        _cand("SPOTIFY", "21.90", date(2026, 6, 25)),
        _cand("SPOTIFY", "21.90", date(2026, 7, 5)),
    ]
    assert group_by_key(cands)["SPOTIFY"].recurring is False


@pytest.mark.asyncio
async def test_memory_pass_applies_consistent_memory_as_memory_source(
    session, test_user, test_workspace, test_account, test_categories
):
    food, transport = test_categories[0].id, test_categories[1].id
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", food)
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", food)
    blank = _tx(session, test_user, test_account, "PADARIA PAO QUENTE")
    guessed = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="provider", category_id=transport)
    same = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="provider", category_id=food)
    other = _tx(session, test_user, test_account, "LOJA SEM MEMORIA")
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="ingest",
        status="running",
    )
    session.add(run)
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))

    rest = await memory_pass(session, test_workspace.id, groups, run.id)

    assert set(rest) == {"LOJA SEM MEMORIA"}
    assert (blank.category_id, blank.category_source) == (food, "memory")
    assert (guessed.category_id, guessed.category_source) == (food, "memory")
    # A provider guess the memory confirms is settled too (as `memory`), so
    # it is not a candidate again once the memory changes.
    assert (same.category_id, same.category_source) == (food, "memory")
    assert (other.category_id, other.category_source) == (None, "none")
    evs = await _events(session)
    assert {e.transaction_id for e in evs} == {blank.id, guessed.id, same.id}
    assert {e.source for e in evs} == {"memory"}
    assert {e.run_id for e in evs} == {run.id}


@pytest.mark.asyncio
async def test_memory_pass_reuses_cached_ai_decision(
    session, test_user, test_workspace, test_account, test_categories
):
    food = test_categories[0].id
    await remember_ai_decision(session, test_workspace.id, "IFOOD", food, 95, "iFood")
    await remember_ai_decision(session, test_workspace.id, "UBER", food, 80, "Uber")
    ifood = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    already = _tx(session, test_user, test_account, "IFOOD*CLUB 4410", source="ai", category_id=food)
    uber = _tx(session, test_user, test_account, "UBER *TRIP")
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))

    rest = await memory_pass(session, test_workspace.id, groups, None)

    assert set(rest) == {"UBER"}
    assert (ifood.category_id, ifood.category_source) == (food, "ai")
    assert uber.category_id is None
    [event] = await _events(session)
    assert event.transaction_id == ifood.id  # `already` was at that category: no-op
    assert already.category_source == "ai"
    assert event.source == "ai"
    assert event.confidence == 95
    assert event.reason == "decisão anterior da IA"


@pytest.mark.asyncio
async def test_memory_pass_ignores_inconsistent_memory(
    session, test_user, test_workspace, test_account, test_categories
):
    a, b = test_categories[0].id, test_categories[1].id
    for cat in (a, b, a):
        await learn_choice(session, test_workspace.id, "LOJA MISTA", cat)
    tx = _tx(session, test_user, test_account, "LOJA MISTA")
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))

    rest = await memory_pass(session, test_workspace.id, groups, None)

    assert set(rest) == {"LOJA MISTA"}
    assert tx.category_id is None


@pytest.mark.asyncio
async def test_pipeline_skips_protected_sources(
    session, test_user, test_workspace, test_account, test_categories
):
    food, transport = test_categories[0].id, test_categories[1].id
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", food)
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", food)
    user_tx = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="user", category_id=transport)
    legacy_tx = _tx(session, test_user, test_account, "PADARIA PAO QUENTE", source="legacy", category_id=transport)
    user_blank = _tx(session, test_user, test_account, "LOJA NOVA", source="user")
    await session.commit()

    run = await run_categorization(session, test_workspace.id, trigger="ingest", use_ai=False)

    for tx in (user_tx, legacy_tx):
        assert (tx.category_id, tx.category_source) == (transport, tx.category_source)
    assert user_tx.category_source == "user" and legacy_tx.category_source == "legacy"
    assert user_blank.category_id is None
    assert await _events(session) == []
    assert await _suggestions(session) == []
    assert run.status == "done"


@pytest.mark.asyncio
async def test_run_without_ai_creates_pending_suggestions(
    session, test_user, test_workspace, test_account, test_categories
):
    food = test_categories[0].id
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", food)
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", food)
    bread = _tx(session, test_user, test_account, "PADARIA PAO QUENTE")
    a = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    b = _tx(session, test_user, test_account, "IFOOD*CLUB 4410")
    guessed = _tx(session, test_user, test_account, "UBER *TRIP", source="provider", category_id=food)
    await session.commit()

    run = await run_categorization(session, test_workspace.id, trigger="ingest", use_ai=False)

    assert run.kind == "classify" and run.trigger == "ingest"
    assert run.status == "done" and run.finished_at is not None
    sugg = await _suggestions(session)
    assert {s.transaction_id for s in sugg} == {a.id, b.id}
    assert {s.merchant_key for s in sugg} == {"IFOOD"}
    assert {s.status for s in sugg} == {"pending"}
    assert {s.category_id for s in sugg} == {None}
    assert {s.run_id for s in sugg} == {run.id}
    assert {s.reason for s in sugg} == {pipeline.NO_AI_REASON}
    assert bread.category_source == "memory"
    assert guessed.category_source == "provider"  # categorized guess: nothing to ask without AI
    assert run.stats == {
        "candidates": 4, "groups": 3, "memory": 1, "applied": 0, "suggested": 2, "skipped": 1,
    }
    settings = await get_settings(session, test_workspace.id)
    assert settings.last_run_at is not None

    again = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=False)

    assert len(await _suggestions(session)) == 2
    assert again.stats["suggested"] == 0


@pytest.mark.asyncio
async def test_run_with_ai_requested_falls_back_and_uses_classifier_seam(
    session, test_user, test_workspace, test_account, monkeypatch
):
    _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()

    run = await run_categorization(session, test_workspace.id, trigger="ingest", use_ai=True)
    assert run.stats["suggested"] == 1

    seen = {}

    async def fake_classifier(session_, workspace_id, groups, run_):
        seen["keys"] = set(groups)

    _tx(session, test_user, test_account, "UBER *TRIP")
    await session.commit()
    monkeypatch.setattr(pipeline, "ai_classifier", fake_classifier)
    await run_categorization(session, test_workspace.id, trigger="ingest", use_ai=True)
    assert seen["keys"] == {"UBER"}


@pytest.mark.asyncio
async def test_suggest_without_ai_skips_transaction_with_pending_suggestion(
    session, test_user, test_workspace, test_account
):
    tx = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))

    assert await suggest_without_ai(session, test_workspace.id, groups, None) == 1
    assert await suggest_without_ai(session, test_workspace.id, groups, None) == 0
    assert [s.transaction_id for s in await _suggestions(session)] == [tx.id]


@pytest.mark.asyncio
async def test_get_settings_creates_defaults_owned_by_workspace_owner(
    session, test_user, test_workspace
):
    editor = User(
        id=uuid.uuid4(), email="editor@example.com", hashed_password="x",
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(editor)
    await session.flush()
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=editor.id, role="editor",
        joined_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    ))
    await session.commit()

    settings = await get_settings(session, test_workspace.id)
    again = await get_settings(session, test_workspace.id)

    assert again.id == settings.id
    assert settings.owner_user_id == test_user.id
    assert settings.enabled is True
    assert settings.auto_apply_threshold == 90
    assert settings.monthly_cap_usd == Decimal("1.00")
    assert settings.llm_connection_id is None
    rows = (await session.execute(select(CategorizationSettings))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_update_settings_applies_only_sent_fields(session, test_workspace):
    await get_settings(session, test_workspace.id)

    settings = await update_settings(
        session, test_workspace.id,
        CategorizationSettingsUpdate(auto_apply_threshold=80, monthly_cap_usd=Decimal("2.50")),
    )
    assert settings.auto_apply_threshold == 80
    assert settings.monthly_cap_usd == Decimal("2.50")
    assert settings.enabled is True

    settings = await update_settings(
        session, test_workspace.id, CategorizationSettingsUpdate(enabled=False)
    )
    assert settings.enabled is False and settings.auto_apply_threshold == 80


def test_settings_update_validates_ranges():
    with pytest.raises(ValueError):
        CategorizationSettingsUpdate(auto_apply_threshold=49)
    with pytest.raises(ValueError):
        CategorizationSettingsUpdate(auto_apply_threshold=101)
    with pytest.raises(ValueError):
        CategorizationSettingsUpdate(monthly_cap_usd=Decimal("-1"))


@pytest.mark.asyncio
async def test_update_settings_refuses_foreign_llm_connection(session, test_user, test_workspace):
    from app.agents.models.connection import LlmConnection

    stranger = User(
        id=uuid.uuid4(), email="stranger@example.com", hashed_password="x",
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(stranger)
    await session.flush()
    mine = LlmConnection(id=uuid.uuid4(), user_id=test_user.id, name="c", kind="anthropic")
    theirs = LlmConnection(id=uuid.uuid4(), user_id=stranger.id, name="c", kind="anthropic")
    openai = LlmConnection(id=uuid.uuid4(), user_id=test_user.id, name="o", kind="openai")
    session.add_all([mine, theirs, openai])
    await session.commit()

    for bad in (theirs.id, openai.id, uuid.uuid4()):
        with pytest.raises(ValueError):
            await update_settings(
                session, test_workspace.id, CategorizationSettingsUpdate(llm_connection_id=bad)
            )
    settings = await update_settings(
        session, test_workspace.id, CategorizationSettingsUpdate(llm_connection_id=mine.id)
    )
    assert settings.llm_connection_id == mine.id
    settings = await update_settings(
        session, test_workspace.id, CategorizationSettingsUpdate(llm_connection_id=None)
    )
    assert settings.llm_connection_id is None


async def test_run_categorization_postgres(postgres_sessions):
    from app.models.account import Account
    from app.models.category import Category
    from app.tasks.categorization_tasks import _categorize
    from tests.test_admin_service import _make_user

    now = datetime.now(timezone.utc)
    async with postgres_sessions() as session:
        user = await _make_user(session, f"pipe_{uuid.uuid4().hex[:8]}@test.com")
        account = Account(
            id=uuid.uuid4(), user_id=user.id, name="Main", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        await session.flush()
        ws = account.workspace_id
        food = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=ws, name="Food")
        session.add(food)
        await session.flush()
        await learn_choice(session, ws, "PADARIA PAO QUENTE", food.id)
        await learn_choice(session, ws, "PADARIA PAO QUENTE", food.id)
        bread = _tx(session, user, account, "PADARIA PAO QUENTE", source="provider")
        ifood = _tx(session, user, account, "IFOOD *REST 8812")
        old = _tx(session, user, account, "LOJA X", created_at=now - timedelta(days=3))
        await session.flush()
        session.add(CategorizationSuggestion(
            id=uuid.uuid4(), workspace_id=ws, transaction_id=old.id, merchant_key="LOJA X",
            status="dismissed", resolved_at=now - timedelta(days=1),
        ))
        fresh = _tx(session, user, account, "LOJA X", created_at=now)
        await session.commit()
        ids = {"bread": bread.id, "ifood": ifood.id, "fresh": fresh.id}

    result = await _categorize(postgres_sessions, ws, "ingest", None)
    assert result["status"] == "done"
    assert result["stats"]["memory"] == 1 and result["stats"]["suggested"] == 2

    async with postgres_sessions() as session:
        bread = await session.get(Transaction, ids["bread"])
        assert bread is not None
        assert (bread.category_id, bread.category_source) == (food.id, "memory")
        pending = {
            s.transaction_id
            for s in await _suggestions(session) if s.status == "pending"
        }
        assert pending == {ids["ifood"], ids["fresh"]}
        settings = await get_settings(session, ws)
        assert settings.owner_user_id == user.id
        assert settings.last_run_at is not None

    again = await _categorize(postgres_sessions, ws, "ingest", None)
    assert again["stats"] == {
        "candidates": 1, "groups": 1, "memory": 0, "applied": 0, "suggested": 0, "skipped": 1,
    }


@pytest.mark.asyncio
async def test_memory_pass_leaves_rows_the_pipeline_already_settled(
    session, test_user, test_workspace, test_account, test_categories
):
    old_choice, new_choice = test_categories[0].id, test_categories[1].id
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", new_choice)
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", new_choice)
    await remember_ai_decision(session, test_workspace.id, "IFOOD", new_choice, 99, "iFood")
    by_memory = _tx(
        session, test_user, test_account, "PADARIA PAO QUENTE", source="memory",
        category_id=old_choice,
    )
    by_ai = _tx(
        session, test_user, test_account, "IFOOD *REST 8812", source="ai", category_id=old_choice,
    )
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))

    rest = await memory_pass(session, test_workspace.id, groups, None)

    assert rest == {}
    assert (by_memory.category_id, by_memory.category_source) == (old_choice, "memory")
    assert (by_ai.category_id, by_ai.category_source) == (old_choice, "ai")
    assert await _events(session) == []


@pytest.mark.asyncio
async def test_memory_pass_ignores_hidden_or_missing_categories(
    session, test_user, test_workspace, test_account, test_categories
):
    hidden = test_categories[0]
    hidden.is_hidden = True
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", hidden.id)
    await learn_choice(session, test_workspace.id, "PADARIA PAO QUENTE", hidden.id)
    await remember_ai_decision(session, test_workspace.id, "IFOOD", uuid.uuid4(), 99, "iFood")
    bread = _tx(session, test_user, test_account, "PADARIA PAO QUENTE")
    ifood = _tx(session, test_user, test_account, "IFOOD *REST 8812")
    await session.commit()
    groups = group_by_key(await collect_candidates(session, test_workspace.id))

    rest = await memory_pass(session, test_workspace.id, groups, None)

    assert set(rest) == {"PADARIA PAO QUENTE", "IFOOD"}
    assert bread.category_id is None and ifood.category_id is None


@pytest.mark.asyncio
async def test_delete_category_moves_or_prunes_memory(
    session, test_user, test_workspace, test_categories
):
    from app.models.categorization import MerchantMemory
    from app.models.category import Category
    from app.services import category_service

    food, transport = test_categories[0].id, test_categories[1].id
    for cat in (food, food, transport):
        await learn_choice(session, test_workspace.id, "LOJA", cat)
    await remember_ai_decision(session, test_workspace.id, "OUTRA", food, 95, "Outra")
    spare = Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Spare",
    )
    session.add(spare)
    await session.flush()
    await learn_choice(session, test_workspace.id, "TERCEIRA", spare.id)
    await session.commit()

    assert await category_service.delete_category(
        session, food, test_workspace.id, transfer_to_id=transport
    )
    assert await category_service.delete_category(session, spare.id, test_workspace.id)

    rows = {
        r.merchant_key: r
        for r in (await session.execute(
            select(MerchantMemory).execution_options(populate_existing=True)
        )).scalars().all()
    }
    assert rows["LOJA"].counts == {str(transport): 3}
    assert rows["OUTRA"].ai_category_id == transport
    assert rows["TERCEIRA"].counts == {}
    assert rows["TERCEIRA"].last_user_category_id is None


@pytest.mark.asyncio
async def test_ingest_looks_only_since_previous_run_but_manual_sees_all(
    session, test_user, test_workspace, test_account
):
    now = datetime.now(timezone.utc)
    session.add(CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="ingest",
        status="done", created_at=now - timedelta(days=1),
    ))
    old = _tx(session, test_user, test_account, "LOJA ANTIGA", created_at=now - timedelta(days=60))
    recent = _tx(
        session, test_user, test_account, "LOJA RECENTE", created_at=now - timedelta(days=10),
    )
    await session.commit()

    since = await pipeline.ingest_since(session, test_workspace.id)
    assert since is not None
    cands = await collect_candidates(session, test_workspace.id, since=since)
    assert {c.tx_id for c in cands} == {recent.id}

    ingest = await run_categorization(session, test_workspace.id, trigger="ingest", use_ai=False)
    assert ingest.stats["candidates"] == 1
    manual = await run_categorization(session, test_workspace.id, trigger="manual", use_ai=False)
    assert manual.stats["candidates"] == 1  # `recent` already waits as a suggestion
    assert {s.transaction_id for s in await _suggestions(session)} == {old.id, recent.id}


@pytest.mark.asyncio
async def test_ingest_since_is_none_before_first_finished_run(session, test_workspace):
    session.add(CategorizationRun(
        id=uuid.uuid4(), workspace_id=test_workspace.id, kind="classify", trigger="ingest",
        status="failed",
    ))
    await session.commit()
    assert await pipeline.ingest_since(session, test_workspace.id) is None


async def test_memory_pointing_at_deleted_category_postgres(postgres_sessions):
    from app.models.account import Account
    from app.models.categorization import MerchantMemory
    from app.models.category import Category
    from app.tasks.categorization_tasks import _categorize
    from tests.test_admin_service import _make_user

    async with postgres_sessions() as session:
        user = await _make_user(session, f"gone_{uuid.uuid4().hex[:8]}@test.com")
        account = Account(
            id=uuid.uuid4(), user_id=user.id, name="Main", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(account)
        await session.flush()
        ws = account.workspace_id
        gone = uuid.uuid4()  # a category deleted behind the memory's back
        session.add(MerchantMemory(
            id=uuid.uuid4(), workspace_id=ws, merchant_key="PADARIA PAO QUENTE",
            counts={str(gone): 5},
        ))
        session.add(Category(id=uuid.uuid4(), user_id=user.id, workspace_id=ws, name="Food"))
        bread = _tx(session, user, account, "PADARIA PAO QUENTE")
        await session.commit()
        bread_id = bread.id

    result = await _categorize(postgres_sessions, ws, "ingest", None)

    assert result["status"] == "done"
    assert result["stats"]["memory"] == 0 and result["stats"]["suggested"] == 1
    async with postgres_sessions() as session:
        bread = await session.get(Transaction, bread_id)
        assert bread is not None and bread.category_id is None
