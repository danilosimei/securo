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
            "JUROS S CAPITAL DE CLIENTES BBDC3",
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


@pytest.mark.parametrize(
    "raw,forbidden",
    [
        ("PIX 123.456.789-09 15/10", "12345678909"),
        ("TED 12345678909 25 JOAO", "12345678909"),
        ("TED 12.345.678/0001-90 25 X", "12345678000190"),
        ("PIX 12345678000190 99", "12345678000190"),
    ],
)
def test_scrub_documents_with_trailing_digit_groups(raw, forbidden):
    out = scrub_documents(raw)
    digits = "".join(c for c in out if c.isdigit())
    assert forbidden not in digits
    assert "123.456.789" not in out and "12.345.678" not in out
    assert "12345678" not in out


def test_scrub_keeps_trailing_group():
    assert scrub_documents("PIX 123.456.789-09 15/10") == "PIX 15/10"


@pytest.mark.parametrize(
    "desc",
    [
        "TED 12345678909 25 JOAO",
        "x|088.124.924-60",
        "Pagamento efetuado|088.124.924-60",
        "Transferência Recebida|088.124.924-60",
        "PAGTO 12.345.678/0001-90 15/10 ACME",
    ],
)
def test_merchant_key_never_leaks_document_digits(desc):
    key = merchant_key(desc)
    assert "088.124.924" not in key and "124.924" not in key
    assert "12345678909" not in key and "12345678000190" not in key
    assert "12.345.678" not in key
    assert "124 924" not in key and "123 456" not in key and "345 678" not in key


def test_replacement_char_in_transfer_kind():
    assert (
        merchant_key(
            "Transfer\ufffdncia Recebida|DANILO SIMEI S NASCIMENTO", payee_name="088.124.924-60"
        )
        == "PIX:DANILO SIMEI S NASCIMENTO"
    )
    assert (
        merchant_key(
            "Transfer\ufffdncia enviada pelo Pix|Eliel Simei do Nascimento",
            payee_name="066.560.644-34",
        )
        == "PIX:ELIEL SIMEI DO NASCIMENTO"
    )


def test_replacement_char_is_deleted_not_spaced():
    assert merchant_key("Aplica\ufffd\ufffdo RDB") == "APLICAO RDB"
    assert merchant_key("Cr\ufffddito Ref. Taxa BTC") == "CRDITO REF TAXA BTC"


def test_nameless_transfer_with_document_payee_is_distinct_per_sender():
    a = merchant_key(
        "Transfer\ufffdncia Recebida", payee_name="088.124.924-60", payee_source="sync"
    )
    b = merchant_key(
        "Transfer\ufffdncia Recebida", payee_name="066.560.644-34", payee_source="sync"
    )
    assert a.startswith("PIX:#") and b.startswith("PIX:#")
    assert a != b
    for key, doc in ((a, "08812492460"), (b, "06656064434")):
        assert doc not in key
    assert a == merchant_key("Transfer\ufffdncia Recebida", payee_name="088.124.924-60")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("RENDIMENTOS DE CLIENTES KNRI11 S/  96", "RENDIMENTOS DE CLIENTES KNRI11"),
        ("RENDIMENTOS DE CLIENTES HGLG11 S/ 106", "RENDIMENTOS DE CLIENTES HGLG11"),
        ("RENDIMENTOS DE CLIENTES KNRI11 S/ 1,100", "RENDIMENTOS DE CLIENTES KNRI11"),
        ("RENDIMENTOS DE CLIENTES KNRI11 S/ 1.100", "RENDIMENTOS DE CLIENTES KNRI11"),
        ("JUROS S/ CAPITAL DE CLIENTES BBDC3 S/ 263", "JUROS S CAPITAL DE CLIENTES BBDC3"),
        ("99 FOOD", "99 FOOD"),
        ("7 ELEVEN 12", "7 ELEVEN"),
    ],
)
def test_quantities_do_not_drift_key(raw, expected):
    assert merchant_key(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("PIX 123 456 789 09 JOAO", "PIX JOAO"),
        ("12 345 678 0001 90", ""),
        ("TED 12 345 678 0001 90 ACME", "TED ACME"),
        ("PIX 123.456.789 09 JOAO", "PIX JOAO"),
    ],
)
def test_scrub_documents_with_spaced_groups(raw, expected):
    assert scrub_documents(raw) == expected


def test_spaced_groups_need_document_shape():
    # Other spaced numbers are not documents: the boundaries still hold.
    assert scrub_documents("PARC 03 DE 10") == "PARC 03 DE 10"
    assert scrub_documents("TED 341 0001 JOAO") == "TED 341 0001 JOAO"
    assert scrub_documents("1234 456 789 09") == "1234 456 789 09"
