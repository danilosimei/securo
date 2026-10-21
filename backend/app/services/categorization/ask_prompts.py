"""What the "ask the AI about a store" chat sends to Claude and reads back.

Pure functions, no database and no client. The request reuses the
classifier's pieces and is ordered for prompt caching:

1. fixed chat instructions (identical for every workspace);
2. the workspace's category tree and the person's examples, the same text
   as the classifier's second block (`prompts.category_context`) -- the
   cache itself is not shared with the batch, whose prefix starts with
   other instructions;
3. what the backend knows about this one store, readable labels plus a
   compact JSON line -- never its merchant key, since a hashed one
   (`PIX:#...`) is a brute-forceable CPF;

each closing a cache breakpoint (one below the model's minimum cacheable
length is ignored without error), followed by the short conversation the
client keeps (it is never stored on the server). Every turn passes through
`sanitize_chat` before it is sent: no CPF/CNPJ, card or account numbers,
and no other run of 5+ digits (dates written in full go too), except an
amount written as money ("R$ 15000", "15.000,00", "25000 reais"), which
is what the person asks about. A bare "15000" still goes: it may as well
be an account or a phone typed without its label (privacy first).

The answer is `{reply, suggestion, note}` under a per-workspace schema whose
category field only accepts that workspace's short ids.
"""
from __future__ import annotations

import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.services.categorization.merchant_key import clean_note, sanitize_chat, sanitize_text
from app.services.categorization.prompts import (
    CLASSIFIER_MODEL,
    CategoryIndex,
    category_context,
    nullable,
    prompt_amount,
    prompt_sample,
)

ASK_MODEL = CLASSIFIER_MODEL
#: A short reply, maybe a suggestion and a note: well under this.
ASK_MAX_TOKENS = 1024
#: The model's reply as shown to the person.
REPLY_LENGTH = 600
#: Each message of the conversation, the person's or the model's.
MESSAGE_LENGTH = 2000
#: Most recent messages re-sent with each question.
HISTORY_WINDOW = 20
#: Example descriptions of the store in the context.
SAMPLES = 3
#: Each field of a new group/category the model proposes.
NEW_CATEGORY_LENGTH = 100
#: What a message becomes when cleaning leaves nothing (the API rejects an
#: empty one, and dropping the turn would break the role alternation).
REMOVED_MESSAGE = "[dados removidos]"
#: Answers that cannot be used.
_UNUSABLE_STOPS = frozenset({"refusal", "max_tokens"})
_ROLES = ("user", "assistant")
_COMPACT = (",", ":")
_EPHEMERAL = {"type": "ephemeral"}

ASK_INSTRUCTIONS = """Você ajuda uma pessoa no Brasil a escolher a categoria desta loja (ou pessoa) nas transações bancárias e de cartão dela. A conversa é só sobre esta loja: a lista de categorias da pessoa, os exemplos de como ela costuma categorizar e os dados da loja vêm a seguir.

## Como conversar
- Responda no idioma em que a pessoa escreve, em texto curto: até 3 frases, sem listas nem markdown.
- Pode perguntar de volta quando faltar contexto (o que foi comprado, quem é a pessoa que recebeu, se é fixo todo mês).
- Na reply, cite a categoria pelo nome, como "Grupo › Categoria" (ex.: "Casa › Jardim"), nunca pelo id (c1, c2...); não mencione os nomes dos campos (q, v, u, s, t, atual, sug, alt, h, nota, suggestion, note).
- Para esta loja você pode propor uma categoria que já existe ou uma nova (em new), inclusive num grupo novo; ela é criada quando a pessoa aceitar.
- Pedidos fora disso você recusa com educação, numa frase, dizendo onde fazer: outras lojas, relatórios, saldos e dados da conta ficam fora desta conversa; regras automáticas, na tela Regras; renomear, apagar ou reorganizar categorias que já existem, na tela Categorias.
- Nunca peça CPF, CNPJ, documentos, número de conta ou de cartão, nem senhas.

## Formato da resposta
Um objeto JSON com estes campos, nesta ordem:
- reply: o que você diz à pessoa.
- suggestion: quando você propõe uma categoria, {"c", "new", "p"}; quando ainda não dá para propor, null.
  - c: id da categoria da lista (ex.: "c4"). Use somente ids da lista desta pessoa; nunca invente um id.
  - new: só quando nenhuma categoria da lista serve: {"g": grupo, "name": nome}, curtos e em português, no estilo da árvore existente. Prefira um grupo que já existe; grupo novo só se nenhum serve. Com new, c fica null. Caso contrário, new é null.
  - p: probabilidade honesta, de 0 a 100, de que esta pessoa aceite a sugestão. Calibre: 95 ou mais só quando a loja é inconfundível e a categoria, óbvia para esta árvore; 70 a 90 com bom indício e alguma dúvida; abaixo de 60 quando é palpite.
- note: a nota completa e atualizada desta loja, para lembrar nas próximas vezes: junte a nota já salva (campo nota dos dados da loja) com o que a pessoa contou agora, sem perder o que já estava nela. No máximo 200 caracteres, no estilo "Aurino: jardineiro semanal, vem toda sexta". Sem documentos, contas ou cartões. Se a pessoa não contou nada novo, null (a nota salva continua como está).

## Como decidir
1. O que a pessoa conta na conversa é o sinal mais forte; depois, a nota já salva da loja e as escolhas anteriores dela (h) e os exemplos.
2. Depois: o que a loja vende, o sentido do dinheiro (t), o valor e as descrições do extrato.
3. Categorias marcadas "(movimentação)" são para dinheiro que só muda de lugar (entre contas, cartão e aplicações); nunca as use para compras.

Responda apenas no formato pedido."""

_STORE_LEGEND = (
    "Campos: q quantas transações; v total em reais; u data da última (AAAA-MM-DD); "
    "s até 3 descrições como aparecem no extrato (documentos e números de conta já removidos); "
    't "d" saída de dinheiro, "c" entrada; atual categoria que as transações têm hoje; '
    "sug categoria sugerida antes; alt segunda opção; h escolhas anteriores desta pessoa "
    "nesta loja, como [id, vezes]; nota o que a pessoa já contou sobre a loja. "
    "Ids da lista de categorias; null quando não há."
)


@dataclass(frozen=True)
class StoreContext:
    """What the backend knows about the store the person asks about."""

    name: str | None
    is_person: bool
    count: int
    total: Decimal
    last_date: date | None
    samples: tuple[str, ...]
    kind: str  # "d" | "c"
    current_category_id: uuid.UUID | None
    suggested_category_id: uuid.UUID | None
    alt_category_id: uuid.UUID | None
    history: tuple[tuple[uuid.UUID, int], ...]
    note: str | None


@dataclass(frozen=True)
class ParsedAnswer:
    """The model's answer, sanitized. `raw` is that sanitized answer as
    compact JSON: the client sends it back as the `assistant` turn."""

    reply: str
    category_id: uuid.UUID | None
    new_category: tuple[str, str] | None
    confidence: int
    note: str | None
    raw: str


def _history(entries: Sequence[tuple[uuid.UUID, int]], index: CategoryIndex) -> list[list[Any]]:
    picks: list[list[Any]] = []
    for category_id, count in entries:
        short = index.to_short(category_id)
        if short is not None and int(count) > 0:
            picks.append([short, int(count)])
    return picks


def render_store_context(ctx: StoreContext, index: CategoryIndex) -> str:
    """The store as readable labels plus one compact JSON line.

    Categories travel as short ids (those no longer in the index become
    null or drop out of `h`); samples and the note lose documents and
    account or card numbers. The merchant key is not part of the context.
    """
    samples: list[str] = []
    for raw in ctx.samples:
        sample = prompt_sample(raw)
        if sample and sample not in samples:
            samples.append(sample)
        if len(samples) >= SAMPLES:
            break
    name = sanitize_text(ctx.name or "").strip()
    if not name:
        name = "pessoa sem nome" if ctx.is_person else "loja sem nome"
    kind = "pessoa" if ctx.is_person else "loja ou empresa"
    data = {
        "q": ctx.count,
        "v": prompt_amount(ctx.total),
        "u": ctx.last_date.isoformat() if ctx.last_date else None,
        "s": samples,
        "t": "c" if ctx.kind == "c" else "d",
        "atual": index.to_short(ctx.current_category_id),
        "sug": index.to_short(ctx.suggested_category_id),
        "alt": index.to_short(ctx.alt_category_id),
        "h": _history(ctx.history, index),
        "nota": clean_note(ctx.note),
    }
    return (
        f"Esta conversa é sobre: {name} ({kind}).\n"
        f"{json.dumps(data, ensure_ascii=False, separators=_COMPACT)}\n"
        f"{_STORE_LEGEND}"
    )


def build_ask_system_blocks(
    index: CategoryIndex, examples: str, ctx: StoreContext
) -> list[dict[str, Any]]:
    """[instructions, tree + examples, store], each closing a cache
    breakpoint: the tree block is byte-identical to the classifier's."""
    texts = (ASK_INSTRUCTIONS, category_context(index, examples), render_store_context(ctx, index))
    return [{"type": "text", "text": text, "cache_control": dict(_EPHEMERAL)} for text in texts]


def build_ask_schema(index: CategoryIndex) -> dict[str, Any]:
    """Strict output schema; `c` accepts only this workspace's ids. `reply`
    comes first so the model talks before it decides."""
    short_ids = index.short_ids
    category_ref: dict[str, Any] = (
        nullable({"type": "string", "enum": list(short_ids)}) if short_ids else {"type": "null"}
    )
    new_category = {
        "type": "object",
        "properties": {"g": {"type": "string"}, "name": {"type": "string"}},
        "required": ["g", "name"],
        "additionalProperties": False,
    }
    suggestion = {
        "type": "object",
        "properties": {
            "c": category_ref,
            "new": nullable(new_category),
            "p": {"type": "integer"},
        },
        "required": ["c", "new", "p"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "suggestion": nullable(suggestion),
            "note": nullable({"type": "string"}),
        },
        "required": ["reply", "suggestion", "note"],
        "additionalProperties": False,
    }


def conversation_messages(turns: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """The last `HISTORY_WINDOW` turns as API messages, starting with the
    person; each content sanitized and cut at `MESSAGE_LENGTH`, or
    `REMOVED_MESSAGE` when nothing is left of it."""
    kept = [(role, content) for role, content in turns if role in _ROLES]
    window = kept[-HISTORY_WINDOW:]
    while window and window[0][0] != "user":
        window = window[1:]
    return [
        {
            "role": role,
            "content": sanitize_chat(content or "")[:MESSAGE_LENGTH].strip() or REMOVED_MESSAGE,
        }
        for role, content in window
    ]


def ask_params(
    system: list[dict[str, Any]], messages: list[dict[str, Any]], schema: dict[str, Any]
) -> dict[str, Any]:
    """Keyword arguments for `client.messages.create`."""
    return {
        "model": ASK_MODEL,
        "max_tokens": ASK_MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        "system": system,
        "messages": messages,
    }


def _confidence(value: Any) -> int:
    """0..100; anything that is not a number is 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if not math.isfinite(value):
        return 0
    return max(0, min(100, round(value)))


def _field(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:limit].rstrip() or None


def _answer_data(message: Any) -> dict[str, Any] | None:
    if getattr(message, "stop_reason", None) in _UNUSABLE_STOPS:
        return None
    for block in getattr(message, "content", None) or []:
        text = getattr(block, "text", None)
        if getattr(block, "type", "text") != "text" or not isinstance(text, str):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


def parse_answer(message: Any, index: CategoryIndex) -> ParsedAnswer | None:
    """The model's answer, or None when it cannot be used (refusal, cut at
    `max_tokens`, not the JSON object asked for, an empty reply).

    An id outside the index is no category; a new category wins over a
    category sent with it (the prompt asks for `c` null with `new`, and
    accepting needs exactly one); a suggestion left with neither is no
    suggestion (confidence 0, null in `raw`). `note` is the store's whole
    note, updated; None leaves the saved one as it is.
    """
    data = _answer_data(message)
    if data is None or not isinstance(data.get("reply"), str):
        return None
    reply = data["reply"].strip()[:REPLY_LENGTH].rstrip()
    if not reply:
        return None
    note = clean_note(data["note"]) if isinstance(data.get("note"), str) else None
    category_id: uuid.UUID | None = None
    new_category: tuple[str, str] | None = None
    confidence = 0
    suggestion = data.get("suggestion")
    if isinstance(suggestion, dict):
        raw_c = suggestion.get("c")
        category_id = index.from_short(raw_c) if isinstance(raw_c, str) else None
        new = suggestion.get("new")
        if isinstance(new, dict):
            group = _field(new.get("g"), NEW_CATEGORY_LENGTH)
            name = _field(new.get("name"), NEW_CATEGORY_LENGTH)
            if group is not None and name is not None:
                new_category = (group, name)
        if new_category is not None:
            category_id = None
        if category_id is not None or new_category is not None:
            confidence = _confidence(suggestion.get("p"))
    clean_suggestion: dict[str, Any] | None = None
    if category_id is not None or new_category is not None:
        clean_suggestion = {
            "c": index.to_short(category_id),
            "new": {"g": new_category[0], "name": new_category[1]} if new_category else None,
            "p": confidence,
        }
    raw = json.dumps(
        {"reply": reply, "suggestion": clean_suggestion, "note": note},
        ensure_ascii=False,
        separators=_COMPACT,
    )
    return ParsedAnswer(
        reply=reply,
        category_id=category_id,
        new_category=new_category,
        confidence=confidence,
        note=note,
        raw=raw,
    )
