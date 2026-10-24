"""`transactions.merchant_key` is kept in step by the model hook."""
import pytest

from app.models.payee import Payee
from app.services.categorization.merchant_key import merchant_key
from tests.test_categorization_api import _tx


@pytest.fixture
async def account(session, test_user, test_account, test_workspace):
    test_account.workspace_id = test_workspace.id
    await session.commit()
    return test_account


@pytest.mark.asyncio
async def test_new_transaction_gets_its_store_key(session, test_user, account):
    tx = _tx(session, test_user, account, "Mlp*Netshoes 02/10")
    await session.commit()
    assert tx.merchant_key == merchant_key("Mlp*Netshoes 02/10") == "NETSHOES"


@pytest.mark.asyncio
async def test_editing_description_changes_the_key(session, test_user, account):
    tx = _tx(session, test_user, account, "Padaria Estrela")
    await session.commit()
    first = tx.merchant_key
    tx.description = "Mercado Dia"
    await session.commit()
    assert first != tx.merchant_key == merchant_key("Mercado Dia")


@pytest.mark.asyncio
async def test_person_pix_gets_a_pix_key(session, test_user, account):
    tx = _tx(session, test_user, account, "Transferência enviada|Maria da Silva")
    await session.commit()
    assert tx.merchant_key.startswith("PIX:")


@pytest.mark.asyncio
async def test_empty_description_is_unknown(session, test_user, account):
    tx = _tx(session, test_user, account, "")
    await session.commit()
    assert tx.merchant_key == "UNKNOWN"


@pytest.mark.asyncio
async def test_payee_decides_the_key(session, test_user, account, test_workspace):
    payee = Payee(user_id=test_user.id, workspace_id=test_workspace.id, name="Padaria Estrela LTDA", source="manual")
    session.add(payee)
    await session.commit()
    tx = _tx(session, test_user, account, "COMPRA 1234", payee_id=payee.id)
    await session.commit()
    assert tx.merchant_key == "P:PADARIA ESTRELA"
    # unloaded payee on a later edit: looked up through the flush connection
    session.expunge(payee)
    session.expire(tx)
    tx.payee_id = None
    await session.commit()
    assert tx.merchant_key == merchant_key("COMPRA 1234")


@pytest.mark.asyncio
async def test_payee_not_in_session_is_looked_up(session, test_user, account, test_workspace):
    payee = Payee(user_id=test_user.id, workspace_id=test_workspace.id, name="Oficina Beta ME", source="manual")
    session.add(payee)
    await session.commit()
    payee_id = payee.id
    session.expunge(payee)
    tx = _tx(session, test_user, account, "COMPRA 9", payee_id=payee_id)
    await session.commit()
    assert tx.merchant_key == "P:OFICINA BETA"
