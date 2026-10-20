import pytest

from app.services.categorization.merchant_key import (
    merchant_key,
    sample_description,
    scrub_documents,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("IFOOD *REST 8812", "IFOOD"),
        ("IFOOD*CLUB 4410", "IFOOD"),
        ("PG *UBER TRIP 1234 SAO PAULO BR", "UBER TRIP"),
        ("MP *MERCADOLIVRE PARC 03/10", "MERCADOLIVRE"),
        ("PAYPAL *NETFLIX.COM", "NETFLIX.COM"),
        ("Padaria São José", "PADARIA SAO JOSE"),
        ("MERCADOPAGO*LOJAXYZ", "LOJAXYZ"),
        ("SUMUP *BARBEARIA DO ZE", "BARBEARIA DO ZE"),
        ("COMPRA CARTAO DEB SUPERMERCADO EXTRA", "SUPERMERCADO EXTRA"),
        ("LOJA AMERICANAS PARCELA 3 DE 10", "LOJA AMERICANAS"),
        ("RENNER 3/10", "RENNER"),
        ("POSTO IPIRANGA   RIO DE JANEIRO RJ", "POSTO IPIRANGA"),
        ("DROGASIL  --  CURITIBA BRA", "DROGASIL"),
        ("PADARIA PE", "PADARIA PE"),
        ("99 POP", "99 POP"),
        ("  spotify   ", "SPOTIFY"),
    ],
)
def test_merchant_key_normalizes(raw, expected):
    assert merchant_key(raw) == expected


def test_merchant_key_never_empty():
    assert merchant_key(None) == "UNKNOWN"
    assert merchant_key("") == "UNKNOWN"
    assert merchant_key("   ") == "UNKNOWN"
    assert merchant_key("PG *") != ""
    assert merchant_key("12345") == "12345"


def test_person_transfer_uses_name():
    assert merchant_key("PIX ENVIADO MARIA S SILVA", is_person_transfer=True) == "PIX:MARIA S SILVA"
    assert (
        merchant_key("x", payee_name="Maria S Silva", is_person_transfer=True)
        == "PIX:MARIA S SILVA"
    )


def test_person_transfer_document_name_is_hashed_without_digits():
    key = merchant_key("PIX ENVIADO 123.456.789-09", is_person_transfer=True)
    assert key.startswith("PIX:#")
    assert "12345678909" not in key
    assert key == merchant_key("x", payee_name="12345678909", is_person_transfer=True)


def test_manual_payee_wins():
    key = merchant_key("DEB AUT 123", payee_name="Academia Smart", payee_source="manual")
    assert key == "P:ACADEMIA SMART"


def test_description_sourced_payee_ignored():
    assert merchant_key("IFOOD *X 1", payee_name="Foo", payee_source="description") == "IFOOD"


def test_document_payee_ignored():
    assert merchant_key("Padaria X", payee_name="088.124.924-60") == "PADARIA X"


UBER = "UBER DO BRASIL TECNOLOGIA LTDA."


@pytest.mark.parametrize(
    "desc,payee,expected",
    [
        (
            "Amazon PSJ AMZ",
            "AMAZON SERVICOS DE VAREJO DO BRASIL LTDA.",
            "P:AMAZON SERVICOS DE VAREJO DO BRASIL",
        ),
        ("Uber Uber *Trip Help.U", UBER, "P:UBER DO BRASIL TECNOLOGIA"),
        ("IOF de volta de Ubr* Pending.Uber.Com", UBER, "P:UBER DO BRASIL TECNOLOGIA"),
        (
            "Transferência Recebida|DANILO SIMEI S NASCIMENTO",
            "088.124.924-60",
            "PIX:DANILO SIMEI S NASCIMENTO",
        ),
        (
            "Transferência enviada pelo Pix|Eliel Simei do Nascimento",
            "066.560.644-34",
            "PIX:ELIEL SIMEI DO NASCIMENTO",
        ),
        (
            "Transferência Recebida|NIMBLOO SOLUCOES DE INTELIGENCIA EM TECNOLOGIA DA INFORMACAO S.A",
            "NIMBLOO SOLUCOES DE INTELIGENCIA EM TECNOLOGIA DA INFORMACAO S.A",
            "P:NIMBLOO SOLUCOES DE INTELIGENCIA EM TECNOLOGIA DA INFORMACAO",
        ),
        ("Pagamento efetuado|SEFAZ PERNAMBUCO-IPV", None, "SEFAZ PERNAMBUCO IPV"),
        ("Ifd*Baker Bar e Restau", None, "BAKER BAR E RESTAU"),
        ("Flexpag*Celpe", None, "CELPE"),
        ("Posto Amigao", None, "POSTO AMIGAO"),
        ("Cod Comercio de Alimen", None, "COD COMERCIO DE ALIMEN"),
        ("Quickmobi", None, "QUICKMOBI"),
        (
            "JUROS S/ CAPITAL DE CLIENTES BBDC3 S/            263",
            None,
            "JUROS S CAPITAL DE CLIENTES BBDC3 S",
        ),
        ("IOF de compra internacional", None, "IOF DE COMPRA INTERNACIONAL"),
        ("Null", None, "UNKNOWN"),
        (None, None, "UNKNOWN"),
        ("", None, "UNKNOWN"),
    ],
)
def test_real_world_samples(desc, payee, expected):
    assert merchant_key(desc, payee_name=payee, payee_source="sync" if payee else None) == expected


def test_scrub_documents():
    assert "12345678000190" not in scrub_documents("TED 12.345.678/0001-90 X")
    assert scrub_documents("TED 12.345.678/0001-90 X") == "TED X"
    assert scrub_documents("CPF 12345678909 ok") == "CPF ok"
    assert scrub_documents("12345678000190") == ""
    assert scrub_documents("PEDIDO 8812") == "PEDIDO 8812"


def test_merchant_key_scrubs_documents():
    assert "12345678000190" not in merchant_key("PAGTO 12.345.678/0001-90 ACME")


def test_sample_description_scrubs_and_truncates():
    assert sample_description(None) == ""
    out = sample_description("A" * 100)
    assert len(out) == 60
    assert "12345678909" not in sample_description("PIX 123.456.789-09 JOAO")
