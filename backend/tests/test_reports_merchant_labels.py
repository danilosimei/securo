import pytest

from app.services.reports.merchant_labels import readable_label


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("STELLANTIS", "Stellantis"),
        ("CASA DE CARNES", "Casa de Carnes"),
        ("P:JOAO DA SILVA", "Joao da Silva"),
        ("PIX:MARIA SOUZA", "Maria Souza"),
        ("XP INVESTIMENTOS", "XP Investimentos"),
        ("TAP AIR PORTUGAL", "Tap Air Portugal"),
        ("SON PET VIDA", "Son Pet Vida"),
        ("PIX:#a1b2c3d4e5", "Pix sem nome"),
        ("UNKNOWN", "Sem nome"),
        (None, "Sem nome"),
        ("", "Sem nome"),
    ],
)
def test_readable_label(key, label):
    assert readable_label(key) == label
