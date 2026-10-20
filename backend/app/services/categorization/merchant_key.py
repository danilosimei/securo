"""Chave de loja: descricao bancaria bruta -> chave estavel para agrupar.

Funcoes puras, sem I/O. Tambem remove CPF/CNPJ antes de qualquer envio ao Claude.
Comportamento conservador: na duvida, a palavra fica.

Precedencia da chave (merchant_key):
1. nome do favorecido (payee) que nao seja documento e cuja origem nao seja
   "description" -> "P:<NOME>" (sem sufixo societario: LTDA, S.A, ME...);
2. transferencia/Pix para contraparte ("Transferencia Recebida|FULANO", ou
   is_person_transfer) -> "PIX:<NOME>" (so documento -> "PIX:#<sha1[:10]>");
3. descricao normalizada (prefixos, parcelas, ids, cidade/UF removidos);
4. "UNKNOWN".
"""

import hashlib
import re
import unicodedata

UNKNOWN_KEY = "UNKNOWN"
SAMPLE_MAX_LENGTH = 60
PERSON_PREFIX = "PIX:"
PAYEE_PREFIX = "P:"

# Prefixos de adquirente/carteira que vem colados a um "*" (ex.: "PG *UBER").
STAR_PREFIXES = (
    "PG",
    "PAG",
    "MP",
    "MERCADOPAGO",
    "MERCPAGO",
    "PAYPAL",
    "EBANX",
    "IFD",
    "PIC",
    "SUMUP",
    "STONE",
    "CIELO",
    "PAGSEGURO",
    "PAGSEG",
    "FLEXPAG",
    "ZP",
    "EC",
    "DL",
)

# Frases genericas de inicio de descricao (sem "*").
LEADING_PHRASES = (
    r"COMPRA\s+(?:NO\s+)?CARTAO(?:\s+(?:DEB|DEBITO|CRED|CREDITO))?",
    r"COMPRA\s+(?:NO\s+)?(?:DEBITO|CREDITO)",
    r"PIX\s+(?:ENVIADO|ENVIADA|RECEBIDO|RECEBIDA)(?:\s+(?:PARA|DE))?",
    r"PIX\s+(?:PARA|DE)",
    r"TRANSFERENCIA\s+(?:ENVIADA|RECEBIDA)(?:\s+(?:PARA|DE))?",
    r"TRANSF(?:ERENCIA)?\s+(?:PARA|DE)",
    r"TED(?:\s+(?:PARA|DE|ENVIADA|RECEBIDA))?",
    r"DOC(?:\s+(?:PARA|DE|ENVIADO|RECEBIDO))?",
)

COUNTRY_CODES = frozenset({"BR", "BRA"})

UF_CODES = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)

# Cidades que costumam vir coladas ao fim da descricao do cartao.
KNOWN_CITIES = (
    "SAO PAULO",
    "RIO DE JANEIRO",
    "BELO HORIZONTE",
    "BRASILIA",
    "CURITIBA",
    "PORTO ALEGRE",
    "SALVADOR",
    "FORTALEZA",
    "RECIFE",
    "GOIANIA",
    "CAMPINAS",
    "BELEM",
    "MANAUS",
    "FLORIANOPOLIS",
    "VITORIA",
    "GUARULHOS",
    "OSASCO",
    "NITEROI",
    "SANTOS",
    "BARUERI",
    "SAO BERNARDO DO CAMPO",
    "SANTO ANDRE",
    "SAO JOSE DOS CAMPOS",
    "RIBEIRAO PRETO",
    "UBERLANDIA",
    "SOROCABA",
    "NATAL",
    "JOAO PESSOA",
    "MACEIO",
    "CUIABA",
    "CAMPO GRANDE",
)

_STAR_PREFIX_RE = re.compile(r"^(?:" + "|".join(STAR_PREFIXES) + r")\s*\*\s*")
_LEADING_PHRASE_RE = re.compile(r"^(?:" + "|".join(LEADING_PHRASES) + r")\b\s*")
# Aplicado antes da limpeza de pontuacao (precisa do "/").
_INSTALLMENT_RE = re.compile(
    r"\bPARC(?:ELA)?\.?\s*\d{1,2}(?:\s*(?:/|DE)\s*\d{1,2})?\b"
    r"|(?<![\d/])\b\d{1,2}/\d{1,2}\b(?![\d/])"
)
_LEGAL_SUFFIX_RE = re.compile(r"[\s,.]+(?:LTDA|S\.?\s*/?\s*A|ME|MEI|EIRELI|EPP)\.?\s*$")
_TRANSFER_KIND_RE = re.compile(r"^(?:TRANSFERENCIA|TRANSF|PIX|TED|DOC)\b")
EMPTY_WORDS = frozenset({"NULL", "NONE", "NAN"})
_TRAILING_ID_RE = re.compile(r"^(?:\d{3,}|[A-Z]*\d{4,}[A-Z0-9]*|#\d+|NR|NUM)$")
_DOCUMENT_RE = re.compile(r"(?<!\d)\d(?:[.\-/ ]?\d){10,13}(?!\d)")
_NON_TEXT_RE = re.compile(r"[^A-Z0-9.*&]+")
_SPACES_RE = re.compile(r"\s+")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _squeeze(text: str) -> str:
    return _SPACES_RE.sub(" ", text).strip()


def scrub_documents(text: str) -> str:
    """Remove CPF/CNPJ (11 ou 14 digitos), com ou sem pontuacao."""

    def _drop(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        return " " if len(digits) in (11, 14) else match.group()

    return _squeeze(_DOCUMENT_RE.sub(_drop, text))


def sample_description(text: str | None) -> str:
    """Descricao de exemplo para prompts: sem documentos e truncada."""
    if not text:
        return ""
    return scrub_documents(text)[:SAMPLE_MAX_LENGTH].rstrip()


def _clean(text: str) -> str:
    """Dobra acentos/caixa e troca pontuacao por espaco (mantem . * &)."""
    folded = _NON_TEXT_RE.sub(" ", _fold(text))
    folded = re.sub(r"(?<![A-Z0-9])\.+|\.+(?![A-Z0-9])", " ", folded)
    return _squeeze(re.sub(r"\s*\*\s*", "*", folded.replace("*", " * ")))


def _strip_leading(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = _LEADING_PHRASE_RE.sub("", text, count=1)
        text = _STAR_PREFIX_RE.sub("", text, count=1)
    return text.strip()


def _cut_star(text: str) -> str:
    if "*" not in text:
        return text
    head, _, tail = text.partition("*")
    return head.strip() or tail.replace("*", " ").strip()


def _strip_trailing(text: str) -> str:
    tokens = text.split()
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        last = tokens[-1]
        if last in COUNTRY_CODES or _TRAILING_ID_RE.match(last):
            tokens.pop()
            changed = True
            continue
        joined = " ".join(tokens)
        # "<cidade> <UF>": a UF so sai quando precedida de cidade conhecida.
        if last in UF_CODES:
            rest = " ".join(tokens[:-1])
            if any(rest.endswith(" " + c) for c in KNOWN_CITIES):
                tokens.pop()
                changed = True
                continue
        for city in KNOWN_CITIES:
            if joined.endswith(" " + city):
                tokens = joined[: -len(city) - 1].split()
                changed = True
                break
    return " ".join(tokens)


def _normalize_description(text: str) -> str:
    folded = _INSTALLMENT_RE.sub(" ", _fold(scrub_documents(text)))
    cleaned = _strip_leading(_clean(folded))
    cleaned = _cut_star(cleaned)
    return _strip_trailing(cleaned)


def _plain_name(text: str) -> str:
    return _clean(text).replace("*", " ").strip()


def _person_key(name_source: str) -> str:
    scrubbed = _strip_leading(_plain_name(scrub_documents(name_source)))
    if re.search(r"[A-Z]", scrubbed):
        return PERSON_PREFIX + _squeeze(scrubbed)
    digits = re.sub(r"\D", "", name_source)
    digest = hashlib.sha1((digits or name_source).encode()).hexdigest()[:10]
    return f"{PERSON_PREFIX}#{digest}"


def _has_letters(text: str) -> bool:
    return bool(re.search(r"[A-Z]", _fold(text)))


def _payee_key(payee_name: str) -> str:
    name = _fold(scrub_documents(payee_name)).strip()
    while True:
        stripped = _LEGAL_SUFFIX_RE.sub("", name)
        if stripped == name or not stripped.strip():
            break
        name = stripped
    return _plain_name(name)


def _split_counterpart(description: str) -> tuple[str, str] | None:
    """ "<tipo>|<contraparte>" (formato Nubank) -> (tipo normalizado, contraparte)."""
    kind, sep, counterpart = description.partition("|")
    if not sep or not counterpart.strip():
        return None
    return _fold(kind).strip(), counterpart.strip()


def merchant_key(
    description: str | None,
    *,
    payee_name: str | None = None,
    payee_source: str | None = None,
    is_person_transfer: bool = False,
) -> str:
    """Chave estavel da loja; nunca vazia."""
    if description is not None and _fold(description).strip() in EMPTY_WORDS:
        description = None
    parts = _split_counterpart(description) if description else None
    payee_ok = bool(payee_name and payee_source != "description" and _has_letters(payee_name))

    if is_person_transfer:
        if payee_ok and payee_name:
            return _person_key(payee_name)
        if parts and (not payee_name or _has_letters(parts[1])):
            return _person_key(parts[1])
        source = payee_name or description
        if source and source.strip():
            return _person_key(source)
    elif payee_ok and payee_name:
        name = _payee_key(payee_name)
        if name:
            return PAYEE_PREFIX + name

    if not description or not description.strip():
        return UNKNOWN_KEY
    if parts:
        kind, counterpart = parts
        if _TRANSFER_KIND_RE.match(kind):
            return _person_key(counterpart)
        description = counterpart
    key = _normalize_description(description)
    if key:
        return key
    return _plain_name(description) or UNKNOWN_KEY
