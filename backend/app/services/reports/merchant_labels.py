"""Readable names for store keys (`transactions.merchant_key`)."""
from typing import Optional

from app.services.categorization.merchant_key import (
    BROKER_FEES_KEY,
    FUND_WITHDRAWAL_TAX_KEY,
    HASHED_PERSON_PREFIX,
    IOF_ABROAD_KEY,
    LENT_SHARES_INCOME_KEY,
    LENT_SHARES_TAX_KEY,
    PAYEE_PREFIX,
    PERSON_PREFIX,
    UNKNOWN_KEY,
)

NO_NAME_LABEL = "Sem nome"
HASHED_PERSON_LABEL = "Pix sem nome"
#: Two letters at most: "XP" is an acronym, but "Pet", "Bar" and "Sol" are words.
ACRONYM_MAX = 2
#: Portuguese connectors stay lower case inside a name.
CONNECTORS = frozenset({"DE", "DA", "DO", "DAS", "DOS", "E"})

#: Stores the key rule groups on purpose (v3): their names are written out.
FIXED_LABELS = {
    IOF_ABROAD_KEY: "IOF de compras no exterior",
    BROKER_FEES_KEY: "Taxas da corretora",
    LENT_SHARES_TAX_KEY: "IR sobre aluguel de ações",
    FUND_WITHDRAWAL_TAX_KEY: "IR sobre resgate de fundos",
    LENT_SHARES_INCOME_KEY: "Aluguel de ações",
}


def readable_label(key: Optional[str]) -> str:
    if key in FIXED_LABELS:
        return FIXED_LABELS[key]
    if not key or key == UNKNOWN_KEY:
        return NO_NAME_LABEL
    if key.startswith(HASHED_PERSON_PREFIX):
        return HASHED_PERSON_LABEL
    for prefix in (PERSON_PREFIX, PAYEE_PREFIX):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    words = key.split()
    if not words:
        return NO_NAME_LABEL
    out = []
    for index, word in enumerate(words):
        if index and word in CONNECTORS:
            out.append(word.lower())
        elif len(word) <= ACRONYM_MAX:
            # Very short words ("XP") are usually acronyms.
            out.append(word.upper())
        else:
            out.append(word.capitalize())
    return " ".join(out)
