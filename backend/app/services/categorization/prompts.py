"""What the categorizer sends to Claude and the shape it must answer in.

The request has three parts, ordered for prompt caching:

1. fixed instructions (identical for every workspace);
2. the workspace's category tree with short ids (`c1`, `c2`...) and up to
   40 of the person's own choices, marked `cache_control` -- the cached
   prefix ends here, so this block must be byte-identical across calls for
   the same workspace state: sorted, no dates, no uuids, no run ids;
3. the batch itself as a compact JSON user message, where each store is an
   item id (`i1`, `i2`...) -- merchant keys never leave the backend, since
   a hashed one (`PIX:#...`) is a brute-forceable CPF.

The answer is constrained by a per-workspace JSON schema whose category
fields only accept that workspace's short ids.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.models.categorization import MerchantMemory
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.services.categorization.memory import DOMINANCE, NOTE_LENGTH
from app.services.categorization.merchant_key import (
    PAYEE_PREFIX,
    PERSON_PREFIX,
    TRAILING_JUNK,
    UNKNOWN_KEY,
    sample_description,
    sanitize_text,
    scrub_documents,
    strip_account_numbers,
)
from app.services.categorization.pipeline import MerchantGroup

CLASSIFIER_MODEL = "claude-haiku-5-5"
#: Person's examples in the cached block.
EXAMPLES_LIMIT = 40
#: Output budget: up to 120 tokens per item plus the envelope, capped so a
#: non-streaming call stays well under the SDK timeout. A ceiling, not a
#: price: billing is by the tokens actually written.
TOKENS_PER_ITEM = 120
TOKENS_BASE = 200
MAX_TOKENS_CAP = 16000
#: Output fields in generation order: name and reason before the decision.
OUTPUT_FIELDS = ("k", "n", "r", "c", "alt", "p", "new")
#: Example store names are cut like the descriptions.
EXAMPLE_NAME_LENGTH = 60

PATH_SEPARATOR = " › "
_SPACES_RE = re.compile(r"\s+")
_CENT = Decimal("0.01")
_ACCOUNT_KINDS = {"credit_card": "cc", "checking": "ca", "savings": "ca"}
#: Suffix on categories that only move money around (`treat_as_transfer`).
TRANSFER_MARK = " (movimentação)"
#: Previous choices per store sent as `h`.
HISTORY_LIMIT = 3

INSTRUCTIONS = """Você categoriza as transações bancárias e de cartão de crédito de uma pessoa no Brasil. As transações chegam agrupadas por loja: decida uma vez por item e a decisão vale para todas as transações daquele item.

## Entrada (mensagem do usuário)
Uma lista JSON de itens com:
- k: id do item; devolva exatamente o mesmo.
- s: até 2 descrições como aparecem no extrato (documentos e números de conta já removidos).
- q: quantas transações o item tem.
- v: valor mediano em reais (sempre positivo).
- t: "d" saída de dinheiro (compra, pagamento, envio); "c" entrada (recebimento, estorno, rendimento).
- m: 1 se repete todo mês com valor parecido (assinatura, conta fixa); 0 se não.
- a: "cc" cartão de crédito; "ca" conta corrente ou poupança; "o" outro tipo de conta.
- h (só às vezes): escolhas anteriores desta pessoa nessa mesma loja, como [id, vezes], da mais frequente para a menos. É um sinal forte: prefira a mais frequente, a não ser que s ou v apontem claramente para outra, e use a segunda como alt.
- o (só às vezes): nota que a pessoa escreveu sobre esta loja; é o sinal mais forte, acima de h.

## Saída
Um objeto por item recebido, sem omitir nenhum, com os campos nesta ordem:
- k: o id do item.
- n: nome limpo e curto da loja, como a marca se escreve ("iFood", "Uber", "Netflix", "Drogasil", "Mercado Livre"); para pessoa, o nome em Title Case ("Maria Souza"). Sem sufixo societário (Ltda, S.A., ME, Eireli, EPP), sem CPF/CNPJ, sem número de pedido, parcela ou conta, sem cidade e sem prefixo de adquirente ("PG *", "MP *", "PagSeguro", "Stone").
- r: motivo em português, no máximo 12 palavras; pense nele antes de escolher c.
- c: id da categoria (ex.: "c4") que esta pessoa escolheria, ou null quando não dá para saber. Use somente ids da lista de categorias desta pessoa; nunca invente um id.
- alt: segunda melhor categoria (id da lista) ou null; nunca igual a c.
- p: probabilidade honesta, de 0 a 100, de que c seja o que esta pessoa escolheria. Calibre: 95 ou mais só quando a loja é inconfundível e a categoria, óbvia para esta árvore; 70 a 90 com bom indício e alguma dúvida; abaixo de 60 quando é palpite. Com c null, p fica no máximo 40.
- new: só quando nenhuma categoria da lista serve razoavelmente: {"g": grupo, "name": nome}, curtos e em português, no estilo da árvore existente (de preferência um grupo que já existe). Caso contrário, null. Com new, c continua sendo a categoria mais próxima, se houver, ou null.

## Como decidir
1. O histórico h e os exemplos desta pessoa são os sinais mais fortes: a mesma loja, ou uma muito parecida, vai para a categoria que ela já usou. Os exemplos mostram também como ela organiza (se ela põe padaria em Mercado, siga isso).
2. Depois: o que a loja vende, o sentido (t), a recorrência (m) e o tipo de conta (a).
3. Na dúvida entre duas categorias, ponha a melhor em c, a outra em alt e baixe p.
4. Categorias marcadas "(movimentação)" são para dinheiro que só muda de lugar (entre contas, cartão e aplicações); nunca as use para compras.

## Descrições comuns em extratos brasileiros
- Pix, TED, DOC ou "Transferência enviada/recebida" de ou para pessoa física: sem contexto (h, um exemplo desta pessoa, um valor fixo todo mês que indique aluguel, salário ou mesada), devolva c null e p no máximo 40, porque só a pessoa sabe o que foi.
- Pix, TED ou transferência para banco, corretora ou carteira digital (Nubank ou NU Pagamentos, Inter, C6, XP, BTG, Rico, Clear, PicPay, Mercado Pago; Itaú, Bradesco, Santander, Caixa ou BB quando a contraparte é o próprio banco), ou com "mesma titularidade" ou "conta própria": movimentação entre contas ou aplicação, nunca compra. Use a categoria de transferência ou de investimento, se existir; senão c null.
- Pix ou TED para outra empresa: compra naquela empresa.
- "Pagamento de fatura", "Pgto fatura cartão": pagamento do cartão, não é um gasto novo. Use a categoria de transferência, se existir; senão c null.
- Boleto, "Pagamento de título", "Débito automático": categorize pelo favorecido (quem recebe).
- "Parcela 3/10", "PARC 03/10": parcela de uma compra; categorize pela loja.
- Saque, "Saque 24h", "Saque Banco24Horas": dinheiro em espécie; use a categoria de saque ou dinheiro, se existir; senão c null.
- IOF (inclusive "IOF compra internacional"), tarifas, anuidade e, com t "d", juros, multa e encargos: impostos e taxas bancárias.
- Com t "c", juros, remuneração, rendimento, "Rend pago aplic", dividendos, JCP ou "Juros s/ capital" de tickers como BBDC3, ITSA4 ou KNRI11 (final 11 costuma ser fundo imobiliário): renda de investimentos.
- RDB, CDB, LCI, LCA, Tesouro, "Aplicação" e "Resgate": dinheiro indo para ou voltando de aplicação; não é gasto nem renda. Use a categoria de investimento ou de transferência, se existir.
- Estorno, reembolso, devolução, cashback: dinheiro voltando (t "c"); use a categoria da compra original quando fizer sentido, ou a de reembolso, se existir.
- Salário, "Pagto salário", proventos, folha: renda do trabalho.
- iFood, Rappi, Zé Delivery: delivery de comida; Uber, 99, Cabify: transporte (mas Uber Eats é delivery).
- Sem Parar, ConectCar, Veloe, pedágio e estacionamento: transporte.
- Mercado Livre, Shopee, Amazon, Magalu, AliExpress: compras, a não ser que os exemplos desta pessoa digam outra coisa.
- Netflix, Spotify, Disney+, Prime Video, YouTube Premium, iCloud, Google One: assinaturas; m 1 reforça.
- Supermercados (Carrefour, Assaí, Pão de Açúcar), farmácias (Drogasil, Raia, Pague Menos), postos (Shell, Ipiranga, BR) e contas de luz, água, gás, internet e celular: use a categoria correspondente desta pessoa.

Responda apenas no formato pedido."""


def _one_line(text: str) -> str:
    return _SPACES_RE.sub(" ", text).strip()


def prompt_sample(text: str | None) -> str:
    """A description as the prompt may carry it: no documents, no account or
    card numbers, at most `SAMPLE_MAX_LENGTH` characters."""
    if not text:
        return ""
    # Documents first: the account/card patterns could take a piece of one
    # and leave the rest unrecognizable.
    return sample_description(strip_account_numbers(scrub_documents(text))).rstrip(TRAILING_JUNK)


@dataclass(frozen=True)
class CategoryIndex:
    """Assignable categories <-> short ids (`c1`, `c2`...) in a stable order."""

    entries: tuple[tuple[str, uuid.UUID, str], ...]
    _by_short: dict[str, uuid.UUID] = field(init=False, repr=False, compare=False)
    _by_id: dict[uuid.UUID, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_short", {s: cid for s, cid, _ in self.entries})
        object.__setattr__(self, "_by_id", {cid: s for s, cid, _ in self.entries})

    @property
    def short_ids(self) -> list[str]:
        return [short for short, _, _ in self.entries]

    def to_short(self, category_id: uuid.UUID | None) -> str | None:
        return self._by_id.get(category_id) if category_id is not None else None

    def from_short(self, short: str | None) -> uuid.UUID | None:
        return self._by_short.get(short) if short else None

    def render(self) -> str:
        """One line per category: `c3 Lazer › Cinema`."""
        return "\n".join(f"{short} {label}" for short, _, label in self.entries)


def build_category_index(
    categories: Iterable[Category],
    groups: Iterable[CategoryGroup],
    *,
    allowed_category_ids: set[uuid.UUID] | None = None,
) -> CategoryIndex:
    """Short ids over the assignable categories, ordered by group position,
    group name, then category name (ungrouped ones last).

    "Assignable" matches `category_service.get_assignable_category_ids`: a
    category hidden directly or through its group is left out. Categories
    that only move money (`treat_as_transfer`) stay, marked TRANSFER_MARK.
    `allowed_category_ids` narrows the index further (a split category's
    rows are redistributed only among its children).
    """
    by_group = {g.id: g for g in groups}
    ranked: list[tuple[tuple[Any, ...], uuid.UUID, str]] = []
    for cat in categories:
        if cat.is_hidden:
            continue
        if allowed_category_ids is not None and cat.id not in allowed_category_ids:
            continue
        name = _one_line(cat.name)
        grp = by_group.get(cat.group_id) if cat.group_id is not None else None
        if grp is not None:
            if grp.is_hidden:
                continue
            group_name = _one_line(grp.name)
            sort_key: tuple[Any, ...] = (
                0, grp.position or 0, group_name.casefold(), group_name, str(grp.id),
                name.casefold(), name, str(cat.id),
            )
            label = f"{group_name}{PATH_SEPARATOR}{name}"
        else:
            sort_key = (1, 0, "", "", "", name.casefold(), name, str(cat.id))
            label = name
        if cat.treat_as_transfer:
            label += TRANSFER_MARK
        ranked.append((sort_key, cat.id, label))
    ranked.sort(key=lambda item: item[0])
    return CategoryIndex(
        tuple((f"c{n}", cid, label) for n, (_, cid, label) in enumerate(ranked, start=1))
    )


def _example_name(key: str) -> str | None:
    """The readable part of a merchant key, or None when it must not be shown.

    Hashed person keys (`PIX:#...`, a CPF digest) and `UNKNOWN` never leave
    the backend; the `P:` marker is dropped and `PIX:` becomes "Pix ".
    """
    if key == UNKNOWN_KEY or key.startswith(PERSON_PREFIX + "#"):
        return None
    if key.startswith(PERSON_PREFIX):
        name = "Pix " + key.removeprefix(PERSON_PREFIX)
    else:
        name = key.removeprefix(PAYEE_PREFIX)
    name = strip_account_numbers(scrub_documents(_one_line(name)))
    name = name[:EXAMPLE_NAME_LENGTH].rstrip()
    return name or None


def _dominant(counts: dict[str, Any] | None) -> tuple[uuid.UUID, int] | None:
    """(category the person picks, total picks) when one choice holds at
    least `DOMINANCE` of them; a single pick counts as a (weak) example."""
    positive: dict[str, int] = {}
    for raw_id, raw_count in (counts or {}).items():
        try:
            count = int(raw_count or 0)
        except (TypeError, ValueError):
            continue
        if count > 0:
            positive[raw_id] = count
    if not positive:
        return None
    total = sum(positive.values())
    top_id = max(sorted(positive), key=lambda k: positive[k])
    if positive[top_id] < DOMINANCE * total:
        return None
    try:
        return uuid.UUID(top_id), total
    except ValueError:
        return None


def clean_note(text: str | None) -> str | None:
    """A store note as the prompt carries it: no documents, no account or
    card numbers, at most `NOTE_LENGTH` characters; None when nothing is
    left."""
    return sanitize_text(text or "")[:NOTE_LENGTH].strip() or None


def build_examples(
    memory_rows: Iterable[MerchantMemory], index: CategoryIndex, limit: int = EXAMPLES_LIMIT
) -> str:
    """The person's own choices as `IFOOD -> c7` lines, most picked first.

    Only stores with a dominant choice of a category still in the index.
    """
    ranked: list[tuple[int, str, str]] = []
    for row in memory_rows:
        name = _example_name(row.merchant_key)
        dominant = _dominant(row.counts)
        if name is None or dominant is None:
            continue
        category_id, total = dominant
        short = index.to_short(category_id)
        if short is None:
            continue
        ranked.append((-total, row.merchant_key, f"{name} -> {short}"))
    ranked.sort()
    lines: list[str] = []
    for _, _, line in ranked:
        if line not in lines:
            lines.append(line)
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def category_context(index: CategoryIndex, examples: str) -> str:
    """The workspace's tree with short ids and the person's examples: the
    cached block shared by the batch and the chat about one store."""
    return (
        "Categorias desta pessoa (id Grupo › Categoria):\n"
        f"{index.render() or '(nenhuma)'}\n\n"
        "Exemplos desta pessoa (loja -> id), dos mais usados para os menos usados:\n"
        f"{examples or '(nenhum ainda)'}"
    )


def build_system_blocks(index: CategoryIndex, examples: str) -> list[dict[str, Any]]:
    """[instructions, tree + examples], each closing a cache breakpoint: the
    instructions stay cached when the tree or the examples change."""
    return [
        {"type": "text", "text": INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
        {
            "type": "text",
            "text": category_context(index, examples),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def prompt_amount(value: Decimal) -> int | float:
    """A value in reais as the prompt carries it: positive, to the cent."""
    cents = abs(value).quantize(_CENT, rounding=ROUND_HALF_UP)
    return int(cents) if cents == cents.to_integral_value() else float(cents)


def _history_entries(entries: Iterable[tuple[str, int]]) -> list[list[str | int]]:
    positive = [(short, int(count)) for short, count in entries if count and int(count) > 0]
    positive.sort(key=lambda e: (-e[1], int(e[0][1:]) if e[0][1:].isdigit() else 0, e[0]))
    return [[short, count] for short, count in positive[:HISTORY_LIMIT]]


def build_history(
    memory_rows: Iterable[MerchantMemory], index: CategoryIndex
) -> dict[str, list[tuple[str, int]]]:
    """Merchant key -> the person's previous picks there as (short id, count),
    most picked first (at most `HISTORY_LIMIT`); categories no longer in the
    index are dropped, and so are stores left with nothing."""
    history: dict[str, list[tuple[str, int]]] = {}
    for row in memory_rows:
        picks: list[tuple[str, int]] = []
        for raw_id, raw_count in (row.counts or {}).items():
            try:
                short = index.to_short(uuid.UUID(raw_id))
                count = int(raw_count or 0)
            except (TypeError, ValueError):
                continue
            if short is not None and count > 0:
                picks.append((short, count))
        entries = _history_entries(picks)
        if entries:
            history[row.merchant_key] = [(str(short), int(count)) for short, count in entries]
    return history


def build_user_message(
    groups: Sequence[MerchantGroup],
    history: dict[str, list[tuple[str, int]]] | None = None,
    notes: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """The batch as compact JSON, plus item id -> merchant key.

    Items are ordered by merchant key so the same batch renders the same
    bytes. Keys stay out of the message; samples lose documents and account
    or card numbers. `history` (merchant key -> previous picks as short id
    and count) becomes the item's `h` when present; `notes` (merchant key ->
    what the person wrote about the store) its `o`, cleaned like the person's
    text and cut at `NOTE_LENGTH`.
    """
    items: list[dict[str, Any]] = []
    ids: dict[str, str] = {}
    for n, grp in enumerate(sorted(groups, key=lambda g: g.key), start=1):
        item_id = f"i{n}"
        ids[item_id] = grp.key
        samples: list[str] = []
        for raw in grp.samples:
            sample = prompt_sample(raw)
            if sample and sample not in samples:
                samples.append(sample)
        item: dict[str, Any] = {
            "k": item_id,
            "s": samples,
            "q": grp.count,
            "v": prompt_amount(grp.median_amount),
            "t": "c" if grp.type == "credit" else "d",
            "m": 1 if grp.recurring else 0,
            "a": _ACCOUNT_KINDS.get(grp.account_type or "", "o"),
        }
        previous = _history_entries((history or {}).get(grp.key, []))
        if previous:
            item["h"] = previous
        note = clean_note((notes or {}).get(grp.key))
        if note:
            item["o"] = note
        items.append(item)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":")), ids


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def build_result_schema(index: CategoryIndex) -> dict[str, Any]:
    """Strict output schema; `c`/`alt` accept only this workspace's ids.

    Properties are ordered name and reason first, so the model writes them
    before deciding (thinking is off; this is its only room to reason).
    """
    short_ids = index.short_ids

    def category_ref() -> dict[str, Any]:
        if not short_ids:
            return {"type": "null"}
        return nullable({"type": "string", "enum": list(short_ids)})

    new_category = {
        "type": "object",
        "properties": {"g": {"type": "string"}, "name": {"type": "string"}},
        "required": ["g", "name"],
        "additionalProperties": False,
    }
    item = {
        "type": "object",
        "properties": {
            "k": {"type": "string"},
            "n": {"type": "string"},
            "r": {"type": "string"},
            "c": category_ref(),
            "alt": category_ref(),
            "p": {"type": "integer"},
            "new": nullable(new_category),
        },
        "required": list(OUTPUT_FIELDS),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item}},
        "required": ["items"],
        "additionalProperties": False,
    }


def classify_params(
    system_blocks: list[dict[str, Any]],
    user_message: str,
    n_items: int,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Keyword arguments for `client.messages.create` (or a batch request).

    No `temperature`: current models reject non-default sampling values.
    """
    return {
        "model": CLASSIFIER_MODEL,
        "max_tokens": min(MAX_TOKENS_CAP, TOKENS_PER_ITEM * n_items + TOKENS_BASE),
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_message}],
    }
