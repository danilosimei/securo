"""Version 3 of the store key: fees and taxes that come with a different
store name on every line are one store each."""
import pytest

from app.services.categorization.merchant_key import MERCHANT_KEY_VERSION, merchant_key
from app.services.reports.merchant_labels import readable_label
from tests.test_merchant_key import REAL_PREFIX_CASES


def test_current_version_is_3():
    assert MERCHANT_KEY_VERSION == 3


@pytest.mark.parametrize(
    "raw",
    [
        "IOF de compra internacional",
        "Iof de Partners ON Booking BV",
        "Best Buy Iof",
        "Guitar Center IOF",
        "IOF gerado por compra em moeda estrangeira",
        "IOF de volta de Ubr* Pending.Uber.Com",
        "IOF DE COMPRA INTERNACIONAL - AMAZON",
        "CTLP*FIRST CLASS VENDI (IOF)",
    ],
)
def test_iof_of_purchases_abroad_is_one_store(raw):
    assert merchant_key(raw) == "IOF COMPRAS NO EXTERIOR"


@pytest.mark.parametrize(
    "raw",
    ["IOF S/ EMPRESTIMO", "IOF CHEQUE ESPECIAL", "IOF ROTATIVO", "Iof financiamento"],
)
def test_iof_on_credit_is_not_a_purchase_abroad(raw):
    assert merchant_key(raw) != "IOF COMPRAS NO EXTERIOR"


@pytest.mark.parametrize(
    "raw",
    [
        "TAXA DE INTERMEDIAÇÃO AZZA3",
        "Taxa de intermediação AZZA3",
        "TAXA DE INTERMEDIACAO DOADOR PETR4",
        "TAXA DE INTERMEDIACAO VALE3",
    ],
)
def test_broker_fees_are_one_store(raw):
    assert merchant_key(raw) == "TAXAS DA CORRETORA"


@pytest.mark.parametrize(
    "raw",
    [
        "DEBITO CBLC IRRF S/ RENDIMENTO DE BTC AZZA3",
        "DEBITO CBLC IRRF S/ RENDIMENTO DE BTC ITSA4",
        "IRRF S/ ALUGUEL DE ACOES BBAS3",
    ],
)
def test_tax_on_lent_shares_is_one_store(raw):
    assert merchant_key(raw) == "IR SOBRE ALUGUEL DE ACOES"


@pytest.mark.parametrize(
    "raw", ["IRRF S/RESGATE FUNDOS XP INVEST", "IRRF S/ RESGATE FUNDOS TREND DI"]
)
def test_tax_on_fund_withdrawals_is_one_store(raw):
    assert merchant_key(raw) == "IR SOBRE RESGATE DE FUNDOS"


@pytest.mark.parametrize(
    "raw",
    [
        "CREDITO REF TAXA DE REMUNERACAO BTC AZZA3",
        "CREDITO REF TAXA DE REMUNERACAO BTC PETR4",
    ],
)
def test_income_from_lent_shares_is_one_store(raw):
    assert merchant_key(raw) == "ALUGUEL DE ACOES"


def test_older_versions_still_give_the_old_keys():
    assert merchant_key("Best Buy Iof", version=2) == "BEST BUY IOF"
    assert merchant_key("Taxa de intermediação AZZA3", version=2) == "TAXA DE INTERMEDIACAO AZZA3"
    assert merchant_key("IOF de volta de Ubr* Pending.Uber.Com", version=1) == "IOF DE VOLTA DE UBR"


@pytest.mark.parametrize(
    "raw,expected", [case for case in REAL_PREFIX_CASES if "IOF" not in case[0]]
)
def test_ordinary_stores_do_not_change(raw, expected):
    assert merchant_key(raw) == merchant_key(raw, version=2)


@pytest.mark.parametrize(
    "raw",
    ["Iofood Delivery", "Netflix.Com", "Taxa de entrega iFood", "Rendimento Poupanca", "Uber *Trip"],
)
def test_lookalikes_do_not_change(raw):
    assert merchant_key(raw) == merchant_key(raw, version=2)


def test_a_payee_does_not_split_the_fee_store():
    assert merchant_key("Guitar Center IOF", payee_name="Guitar Center", payee_source="sync") == (
        "IOF COMPRAS NO EXTERIOR"
    )


@pytest.mark.parametrize(
    "key,label",
    [
        ("IOF COMPRAS NO EXTERIOR", "IOF de compras no exterior"),
        ("TAXAS DA CORRETORA", "Taxas da corretora"),
        ("IR SOBRE ALUGUEL DE ACOES", "IR sobre aluguel de ações"),
        ("IR SOBRE RESGATE DE FUNDOS", "IR sobre resgate de fundos"),
        ("ALUGUEL DE ACOES", "Aluguel de ações"),
    ],
)
def test_grouped_stores_have_readable_names(key, label):
    assert readable_label(key) == label
