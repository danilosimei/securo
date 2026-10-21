"""Chave de loja: descricao bancaria bruta -> chave estavel para agrupar.

Funcoes puras, sem I/O. Tambem remove CPF/CNPJ antes de qualquer envio ao Claude.
Comportamento conservador: na duvida, a palavra fica.

Precedencia da chave (merchant_key):
1. nome do favorecido (payee) que nao seja documento e cuja origem nao seja
   "description" nem "ai" -> "P:<NOME>" (sem sufixo societario: LTDA, S.A, ME...);
2. transferencia/Pix para contraparte ("Transferencia Recebida|FULANO", ou
   is_person_transfer) -> "PIX:<NOME>" (so documento -> "PIX:#<sha1[:10]>");
3. descricao normalizada (prefixos, parcelas, ids, cidade/UF removidos);
4. "UNKNOWN".

Versoes da regra da descricao (payee e Pix nao mudam entre elas):
- 1: so os prefixos de STAR_PREFIXES saem; outro "<CABECA>*" vira a loja
  ("Mlp*Netshoes" -> "MLP", juntando lojas diferentes).
- 2 (atual): um prefixo curto de 2 a 5 letras antes do "*" tambem sai
  ("Mlp*Netshoes" -> "NETSHOES"), salvo marcas (BRAND_STAR_PREFIXES); se o
  resto e generico (vazio, "PENDING...", so pais/UF/numeros) a loja e a
  marca do prefixo (PREFIX_BRANDS) ou o proprio prefixo ("IFD*BR" -> "IFOOD").
  O nome repetido em volta de um hifen fica uma vez so
  ("Mlp *Kabum-Kabum" -> "KABUM", como "Mlp*Kabum").
  A versao 1 continua disponivel para achar as chaves gravadas antes.
"""

import hashlib
import re
import unicodedata

MERCHANT_KEY_VERSION = 2
UNKNOWN_KEY = "UNKNOWN"
SAMPLE_MAX_LENGTH = 60
PERSON_PREFIX = "PIX:"
PAYEE_PREFIX = "P:"
#: A person key built from a document digest (`PIX:#...`): brute-forceable
#: into a CPF, so it never names anybody, to a person or to the model.
HASHED_PERSON_PREFIX = PERSON_PREFIX + "#"
# Origens de payee que nao definem a loja: o nome veio da propria descricao
# (sync com payee_source="description") ou foi o nome limpo que a IA deu a
# partir dela. Usa-los como chave mudaria a chave da linha depois de
# categorizada e separaria a loja da memoria dela.
DERIVED_PAYEE_SOURCES = frozenset({"description", "ai"})

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

# v2: cabecas curtas antes do "*" que sao a propria marca (ficam como a loja).
BRAND_STAR_PREFIXES = frozenset({"UBER", "IFOOD", "CASAR"})
# v2: marca de um prefixo quando o resto da descricao e generico.
PREFIX_BRANDS = {"UBR": "UBER", "IFD": "IFOOD"}

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

_STAR_PREFIX_RE = re.compile(r"^(" + "|".join(STAR_PREFIXES) + r")\s*\*\s*")
# v2, depois de _clean (que ja colou o "*"): um token unico de 2 a 5 letras.
_SHORT_STAR_PREFIX_RE = re.compile(r"^([A-Z]{2,5})\*")
_PENDING_REST_RE = re.compile(r"^(?:PENDING|PENDENTE)\b")
# v2, no texto dobrado (antes de _clean, que apaga o hifen): o mesmo nome
# repetido dos dois lados de um hifen ("KABUM-KABUM", "PAO DOCE - PAO DOCE")
# fica uma vez so. Repeticao sem hifen ("PAO PAO DOCE") nao muda.
_HYPHEN_REPEAT_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]+(?: [A-Z0-9]+)*) ?- ?\1(?![A-Z0-9])")
_LEADING_PHRASE_RE = re.compile(r"^(?:" + "|".join(LEADING_PHRASES) + r")\b\s*")
# Aplicado antes da limpeza de pontuacao (precisa do "/").
_INSTALLMENT_RE = re.compile(
    r"\bPARC(?:ELA)?\.?\s*\d{1,2}(?:\s*(?:/|DE)\s*\d{1,2})?\b"
    r"|(?<![\d/])\b\d{1,2}/\d{1,2}\b(?![\d/])"
)
_LEGAL_SUFFIX_RE = re.compile(r"[\s,.]+(?:LTDA|S\.?\s*/?\s*A|ME|MEI|EIRELI|EPP)\.?\s*$")
# U+FFFD (encoding corrompido) e removido no fold, entao "TRANSFERNCIA" tambem casa.
_TRANSFER_KIND_RE = re.compile(r"^(?:TRANSF\w*|PIX|TED|DOC)\b")
NAMELESS_TRANSFER_MAX_TOKENS = 4
_NUMERIC_TOKEN_RE = re.compile(r"^\d+(?:[.,]\d+)*$")
EMPTY_WORDS = frozenset({"NULL", "NONE", "NAN"})
_TRAILING_ID_RE = re.compile(r"^(?:\d{3,}|[A-Z]*\d{4,}[A-Z0-9]*|#\d+|NR|NUM)$")
# Formas explicitas de CNPJ (14: 2-3-3-4-2) e CPF (11: 3-3-3-2). Entre os
# grupos, a pontuacao do documento (. / -) ou um espaco ("123 456 789 09");
# nunca colado a outro digito.
_DOCUMENT_RE = re.compile(
    r"(?<!\d)(?:"
    r"\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}"
    r"|\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}"
    r")(?!\d)"
)
_NON_TEXT_RE = re.compile(r"[^A-Z0-9.*&]+")
_SPACES_RE = re.compile(r"\s+")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.replace("�", ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _squeeze(text: str) -> str:
    return _SPACES_RE.sub(" ", text).strip()


# Prompt samples only (merchant keys are untouched): branch/account numbers
# after their label, card-number shapes, "final 1234", and any run of 5+
# digits with . - / * separators (masked documents like ***.456.789-**).
# Bank codes (3 digits, e.g. "TED 341") stay: public, and a useful signal.
_ACCOUNT_TOKEN_RE = re.compile(
    r"\b(?:AG|AGENCIA|AGÊNCIA|CC|C/C|CONTA|CT)\b\.?:?\s*[.\-/]*\d[\d.\-/]*", re.IGNORECASE
)
_CARD_RE = re.compile(
    r"(?<![\w*])(?:\d{4}|\*{4}|[Xx]{4})(?:[ .\-]?(?:\d{4}|\*{4}|[Xx]{4})){2,3}(?![\w*])"
)
_CARD_FINAL_RE = re.compile(r"\bFINAL\s*\d{4}\b", re.IGNORECASE)
_NUMBER_RUN_RE = re.compile(r"[\d*][\d.\-/*]*")
NUMBER_RUN_MIN_DIGITS = 5
TRAILING_JUNK = " |-/:"


def _drop_long_number(match: re.Match[str]) -> str:
    token = match.group(0)
    digits = sum(ch.isdigit() for ch in token)
    return " " if digits >= NUMBER_RUN_MIN_DIGITS else token


def strip_account_numbers(text: str) -> str:
    """Remove branch/account/card numbers and long digit runs (prompt text only)."""
    text = _ACCOUNT_TOKEN_RE.sub(" ", text)
    text = _CARD_RE.sub(" ", text)
    text = _CARD_FINAL_RE.sub(" ", text)
    text = _NUMBER_RUN_RE.sub(_drop_long_number, text)
    return _squeeze(text).rstrip(TRAILING_JUNK)


def sanitize_text(text: str) -> str:
    """Free text as a prompt or a note may carry it: no documents, no account
    or card numbers. Not truncated."""
    return strip_account_numbers(scrub_documents(text))


def scrub_documents(text: str) -> str:
    """Remove CPF/CNPJ (11 ou 14 digitos), com ou sem pontuacao ou espacos."""

    return _squeeze(_DOCUMENT_RE.sub(" ", text))


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


def _strip_leading_tracked(text: str) -> tuple[str, str | None]:
    """Tira frases e prefixos de STAR_PREFIXES do inicio; devolve tambem o
    ultimo prefixo retirado."""
    prefix = None
    previous = None
    while previous != text:
        previous = text
        text = _LEADING_PHRASE_RE.sub("", text, count=1)
        match = _STAR_PREFIX_RE.match(text)
        if match:
            prefix = match.group(1)
            text = text[match.end() :]
    return text.strip(), prefix


def _strip_leading(text: str) -> str:
    return _strip_leading_tracked(text)[0]


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


def _drop_quantities(text: str) -> str:
    """Tira tokens numericos (nao-iniciais) e o "S" solto de "S/ <n>"."""
    out: list[str] = []
    for index, token in enumerate(text.split()):
        if index > 0 and _NUMERIC_TOKEN_RE.match(token):
            if len(out) > 1 and out[-1] == "S":
                out.pop()
            continue
        out.append(token)
    return " ".join(out)


def _finish(text: str) -> str:
    return _strip_trailing(_drop_quantities(_cut_star(text)))


def _is_generic_rest(key: str) -> bool:
    """Resto que nao identifica loja: vazio, "PENDING..." ou so pais/UF/numeros."""
    if _PENDING_REST_RE.match(key):
        return True
    return all(
        token in COUNTRY_CODES or token in UF_CODES or _NUMERIC_TOKEN_RE.match(token)
        for token in key.split()
    )


def _normalize_description(text: str, version: int) -> str:
    folded = _INSTALLMENT_RE.sub(" ", _fold(scrub_documents(text)))
    if version >= 2:
        folded = _HYPHEN_REPEAT_RE.sub(r"\1", _squeeze(folded))
    cleaned, prefix = _strip_leading_tracked(_clean(folded))
    if version < 2:
        return _finish(cleaned)
    match = _SHORT_STAR_PREFIX_RE.match(cleaned)
    if match and match.group(1) not in BRAND_STAR_PREFIXES:
        prefix = match.group(1)
        cleaned = cleaned[match.end() :]
    key = _finish(cleaned)
    if prefix and _is_generic_rest(key):
        return PREFIX_BRANDS.get(prefix, prefix)
    return key


def _plain_name(text: str) -> str:
    return _clean(text).replace("*", " ").strip()


def _person_key(name_source: str) -> str:
    scrubbed = _strip_leading(_plain_name(scrub_documents(name_source)))
    if re.search(r"[A-Z]", scrubbed):
        return PERSON_PREFIX + _squeeze(scrubbed)
    digits = re.sub(r"\D", "", name_source)
    digest = hashlib.sha1((digits or name_source).encode()).hexdigest()[:10]
    return f"{HASHED_PERSON_PREFIX}{digest}"


def is_unnamed_key(key: str) -> bool:
    """Whether the key carries no name that may be shown: `UNKNOWN` or a
    hashed person key."""
    return key == UNKNOWN_KEY or key.startswith(HASHED_PERSON_PREFIX)


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
    version: int = MERCHANT_KEY_VERSION,
) -> str:
    """Chave estavel da loja; nunca vazia. version=1 reproduz as chaves
    gravadas antes da regra de prefixo curto (ver docstring do modulo)."""
    if description is not None and _fold(description).strip() in EMPTY_WORDS:
        description = None
    parts = _split_counterpart(description) if description else None
    derived_payee = payee_source in DERIVED_PAYEE_SOURCES
    payee_ok = bool(payee_name and not derived_payee and _has_letters(payee_name))

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
    folded_description = _fold(description).strip()
    if (
        payee_name
        and not derived_payee
        and not payee_ok
        and not parts
        and _TRANSFER_KIND_RE.match(folded_description)
        and len(folded_description.split()) <= NAMELESS_TRANSFER_MAX_TOKENS
    ):
        return _person_key(payee_name)
    if parts:
        kind, counterpart = parts
        if _TRANSFER_KIND_RE.match(kind):
            return _person_key(counterpart)
        description = counterpart
    key = _normalize_description(description, version)
    if key:
        return key
    return _plain_name(scrub_documents(description)) or UNKNOWN_KEY
