"""Payee edits that bypass the transaction hook still keep `merchant_key` right."""
import uuid

import pytest
from sqlalchemy import select

from app.models.payee import Payee
from app.models.transaction import Transaction
from app.schemas.payee import PayeeUpdate
from app.services import payee_service
from app.services.categorization.merchant_key import merchant_key
from tests.test_categorization_api import _tx


@pytest.fixture
async def account(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    await session.commit()
    return test_account


def _payee(session, user, ws, name):
    payee = Payee(id=uuid.uuid4(), user_id=user.id, workspace_id=ws.id, name=name, source="manual")
    session.add(payee)
    return payee


async def _key(session, tx_id):
    session.expire_all()
    return (await session.execute(select(Transaction.merchant_key).where(Transaction.id == tx_id))).scalar_one()


@pytest.mark.asyncio
async def test_merge_gives_absorbed_transactions_the_new_name_key(
    session, test_user, test_workspace, account
):
    keep = _payee(session, test_user, test_workspace, "Padaria Estrela LTDA")
    gone = _payee(session, test_user, test_workspace, "Oficina Beta ME")
    keep_id, gone_id, ws_id = keep.id, gone.id, test_workspace.id
    await session.commit()
    tx = _tx(session, test_user, account, "COMPRA 1", payee_id=gone_id)
    await session.commit()
    assert await _key(session, tx.id) == "P:OFICINA BETA"

    await payee_service.merge_payees(session, ws_id, keep_id, [gone_id])

    assert await _key(session, tx.id) == "P:PADARIA ESTRELA"


@pytest.mark.asyncio
async def test_rename_payee_changes_the_key(session, test_user, test_workspace, account):
    payee = _payee(session, test_user, test_workspace, "Padaria Estrela LTDA")
    payee_id, ws_id = payee.id, test_workspace.id
    await session.commit()
    tx = _tx(session, test_user, account, "COMPRA 2", payee_id=payee_id)
    await session.commit()
    assert await _key(session, tx.id) == "P:PADARIA ESTRELA"

    await payee_service.update_payee(
        session, payee_id, ws_id, PayeeUpdate(name="Mercado Dia LTDA")
    )

    assert await _key(session, tx.id) == "P:MERCADO DIA"


@pytest.mark.asyncio
async def test_editing_other_fields_leaves_keys_alone(session, test_user, test_workspace, account):
    payee = _payee(session, test_user, test_workspace, "Padaria Estrela LTDA")
    await session.commit()
    tx = _tx(session, test_user, account, "COMPRA 3", payee_id=payee.id)
    await session.commit()
    await payee_service.update_payee(
        session, payee.id, test_workspace.id, PayeeUpdate(notes="ok")
    )
    assert await _key(session, tx.id) == "P:PADARIA ESTRELA"


@pytest.mark.asyncio
async def test_delete_payee_falls_back_to_description_key(
    session, test_user, test_workspace, account
):
    payee = _payee(session, test_user, test_workspace, "Padaria Estrela LTDA")
    await session.commit()
    tx = _tx(session, test_user, account, "COMPRA 4", payee_id=payee.id)
    await session.commit()

    assert await payee_service.delete_payee(session, payee.id, test_workspace.id)

    assert await _key(session, tx.id) == merchant_key("COMPRA 4")


@pytest.mark.asyncio
async def test_bulk_delete_falls_back_to_description_key(
    session, test_user, test_workspace, account
):
    payee = _payee(session, test_user, test_workspace, "Padaria Estrela LTDA")
    await session.commit()
    tx = _tx(session, test_user, account, "COMPRA 5", payee_id=payee.id)
    await session.commit()

    assert await payee_service.bulk_delete_payees(session, test_workspace.id, [payee.id]) == 1

    assert await _key(session, tx.id) == merchant_key("COMPRA 5")
