"""The category organizer: Opus proposes a leaner tree, the person approves it op by op.

    collect_input: the tree (short ids `g1`/`c1`) and, for the last 12
      months, per-category and per-store aggregates (`i1`: a readable name,
      the store's category, count, total) -- never a merchant key, a raw
      description, a document or a uuid
      -> monthly cap: month's spend + estimate > cap -> run `capped`
      -> one Opus call (adaptive thinking, effort medium, server-side
         fallbacks; never a Message Batch, which rejects `fallbacks`)
      -> refusal / cut-off / unreadable answer -> run `failed`, with a
         friendly `detail["message"]`
      -> `filter_ops`: unknown ids, a third level, a new category under
         the usage floor and groups outside 2-6 categories are dropped
      -> `run.detail["ops"]`, each `pending`

    apply_op / reject_op, one op at a time, through the category services
      (`commit=False`, so an op is all or nothing in the caller's
      transaction); every op drops the model's cached decisions
      (`forget_ai_decisions`). `split_category` keeps the old category's id
      as its first child, so budgets, rules, recurring entries and the
      Pluggy map still point somewhere valid, and queues a `redistribute`
      run that re-files the old category's rows among the children only
      (the Haiku classifier, `user` rows included: the person approved the
      split). Structural changes have no automatic undo (spec section 9);
      the rows they move do, through their events.

Nothing here logs the request, the answer or the key; nothing commits but
the usage of a paid call.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import and_, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.categorization import (
    CategorizationRun,
    CategorizationSuggestion,
    MerchantMemory,
)
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.schemas.category_group import CategoryGroupCreate, CategoryGroupUpdate
from app.services import category_group_service, category_service
from app.services.categorization import classifier, memory
from app.services.categorization.claude_client import create_beta_message, resolve_client
from app.services.categorization.costs import PRICES, cost_usd
from app.services.categorization.events import StagedChange, stage_change, write_staged
from app.services.categorization.merchant_key import merchant_key, scrub_documents
from app.services.categorization.pipeline import (
    Candidate,
    blocking_suggestion,
    group_by_key,
    stored_key,
)
from app.services.categorization.prompts import (
    _example_name,
    prompt_sample,
    strip_account_numbers,
)
from app.services.categorization.settings import get_settings

logger = logging.getLogger(__name__)

ORGANIZER_MODEL = "claude-opus-5-5"
#: `fallbacks: "default"` (the scalar form) goes with this header only.
ORGANIZER_BETAS = ["server-side-fallback-2026-07-01"]
ORGANIZER_MAX_TOKENS = 16000
OP_KINDS = (
    "create_group", "create_category", "rename", "move_category", "merge", "split_category",
)
#: How far back the usage summary looks.
WINDOW = timedelta(days=365)
#: A new category needs this much use in the window (either one).
MIN_NEW_TX = 3
MIN_NEW_TOTAL = Decimal("200")
#: Categories per group the proposal may leave.
GROUP_MIN = 2
GROUP_MAX = 6
#: Stores in the summary, the ones that move the most money first.
STORE_LIMIT = 300
NAME_LENGTH = 100
STORE_NAME_LENGTH = 60
WHY_LENGTH = 200
REASON_LENGTH = 120
#: A busy organizer run of the workspace (a new request returns it).
BUSY_STATUSES = ("queued", "running")
ORGANIZER_KINDS = ("organize", "redistribute")
ABANDONED_ERROR = "abandoned"
#: Friendly text for each way a run can end without a proposal.
MESSAGES = {
    "no_ai_connection": "Conecte uma chave da Anthropic nas configurações para usar o organizador.",
    "refusal": "O Claude não aceitou analisar estas categorias agora. Tente de novo mais tarde.",
    "incomplete": "A resposta do Claude veio incompleta. Tente de novo.",
    "invalid_answer": "Não deu para ler a resposta do Claude. Tente de novo.",
    "capped": "O teto mensal de gastos com IA foi atingido. Aumente o teto ou tente no mês que vem.",
    "categories_gone": "As categorias desta divisão não existem mais.",
}
_SHORT_RE = re.compile(r"^[cgi]\d+$")
_CENT = Decimal("0.01")

ORGANIZER_INSTRUCTIONS = """Você organiza a árvore de categorias de gastos de uma pessoa no Brasil. A árvore tem dois níveis: grupos amplos (ex.: "Lazer", "Casa", "Transporte") e, dentro de cada grupo, categorias específicas (ex.: Lazer → Assinaturas, Ingressos, Cinema). Proponha uma árvore enxuta e útil como uma lista de operações; a pessoa vai aprovar ou recusar cada uma.

## Entrada (mensagem do usuário, JSON)
- groups: grupos atuais, cada um com g (id do grupo), name e cats (as categorias do grupo).
- ungrouped: categorias sem grupo.
- Cada categoria tem c (id), name, q (quantas transações nos últimos 12 meses), v (total em reais nos últimos 12 meses) e, quando ela só movimenta dinheiro entre contas, t: 1.
- stores: as lojas dos últimos 12 meses, das que mais movimentam para as que menos. Cada uma tem k (id da loja), n (nome), c (id da categoria atual ou null), q (transações), v (total em reais) e t ("d" saída de dinheiro, "c" entrada).

## Operações
Toda operação traz todos os campos; use null (ou [] em children) no que não se aplica.
- create_group: cria um grupo. name = nome do grupo. Só tem sentido se outras operações puserem pelo menos 2 categorias nele.
- create_category: cria uma categoria e leva para ela as lojas listadas. group = id de um grupo existente ("g2") ou nome de um grupo novo; name = nome da categoria; children = ids das lojas (k) que vão para ela.
- rename: renomeia. target = id da categoria ou do grupo; name = nome novo.
- move_category: muda a categoria de grupo. target = id da categoria; group = id de grupo existente ou nome de grupo novo.
- merge: junta duas categorias. target = a categoria que deixa de existir; into = a categoria que fica com tudo.
- split_category: transforma uma categoria em várias mais específicas dentro de um grupo. target = id da categoria; name = nome do grupo que recebe as filhas (um grupo existente ou novo; pode ser o nome atual da categoria); children = nomes das categorias filhas, de 2 a 6. A primeira filha herda o histórico, os orçamentos e as regras da categoria atual, então ponha nela o uso mais comum. As transações da categoria são redistribuídas entre as filhas depois.
Campos comuns: id ("o1", "o2"... na ordem), why (motivo curto em português, até 20 palavras) e impact (tx: transações afetadas; total: valor em reais afetado).

## Regras
1. Só dois níveis: grupo → categoria. Nunca ponha grupo dentro de grupo nem categoria dentro de categoria.
2. Grupos amplos, categorias específicas. Todo grupo que uma operação tocar termina com 2 a 6 categorias.
3. Reaproveite antes de criar: prefira renomear, mover ou juntar categorias que já existem.
4. Categoria nova só com pelo menos 3 transações ou R$ 200 nos últimos 12 meses (some q e v das lojas que vão para ela). Numa divisão, a categoria atual precisa ter uso para todas as filhas.
5. Junte categorias quase vazias ou repetidas; divida as grandes e misturadas.
6. Categorias com t: 1 só podem ser renomeadas ou mudar de grupo; nunca as junte com categorias de gasto ou de renda.
7. Nomes curtos, em português, no estilo da árvore atual. Não use nome de loja como categoria ("Uber" não; "Transporte por app" sim).
8. Menos é mais: proponha só o que deixa a árvore claramente melhor. Uma árvore que já está boa recebe poucas operações, ou nenhuma.
9. Use somente ids que aparecem na entrada.

Responda apenas no formato pedido."""


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


#: Strict output schema (constant, so the compiled grammar stays cached).
#: Ids are checked by `filter_ops`, not by enums: the schema is the same
#: for every workspace.
ORGANIZER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "op": {"type": "string", "enum": list(OP_KINDS)},
                    "target": _nullable({"type": "string"}),
                    "group": _nullable({"type": "string"}),
                    "name": _nullable({"type": "string"}),
                    "children": {"type": "array", "items": {"type": "string"}},
                    "into": _nullable({"type": "string"}),
                    "why": {"type": "string"},
                    "impact": {
                        "type": "object",
                        "properties": {
                            "tx": {"type": "integer"},
                            "total": {"type": "number"},
                        },
                        "required": ["tx", "total"],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "id", "op", "target", "group", "name", "children", "into", "why", "impact",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["ops"],
    "additionalProperties": False,
}


class NotFound(Exception):
    """No organizer run, or no such op in it."""


class OpConflict(Exception):
    """The op cannot be applied (already decided, or the tree moved on)."""


# --- the summary sent to Opus ---------------------------------------------------


@dataclass
class CategoryStat:
    name: str
    group: str | None
    tx: int = 0
    total: Decimal = Decimal(0)
    transfer: bool = False


@dataclass
class StoreStat:
    name: str
    tx: int = 0
    total: Decimal = Decimal(0)


@dataclass
class OrganizerStats:
    """What `filter_ops` checks a proposal against, by short id."""

    groups: dict[str, str]
    categories: dict[str, CategoryStat]
    stores: dict[str, StoreStat] = field(default_factory=dict)


@dataclass
class OrganizerInput:
    text: str
    stats: OrganizerStats
    #: short id -> uuid (groups and categories).
    ids: dict[str, str]
    #: store short id -> {"key": merchant key, "tx": [row ids]} (backend only).
    stores: dict[str, dict[str, Any]]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _norm(text: str | None) -> str:
    """Name comparison: case, accents and spacing do not count."""
    folded = unicodedata.normalize("NFKD", _one_line(text or "")).casefold()
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _money(value: Decimal) -> int | float:
    cents = abs(value).quantize(_CENT, rounding=ROUND_HALF_UP)
    return int(cents) if cents == cents.to_integral_value() else float(cents)


def _store_name(key: str, remembered: str | None, descriptions: Counter[str]) -> str:
    """A readable store name: the model's clean name, the readable key, or a
    scrubbed description -- never a hashed key or a document."""
    for candidate in (remembered, _example_name(key)):
        if candidate:
            cleaned = strip_account_numbers(scrub_documents(_one_line(candidate)))
            if cleaned:
                return cleaned[:STORE_NAME_LENGTH].rstrip()
    for description, _ in descriptions.most_common():
        sample = prompt_sample(description)
        if sample:
            return sample[:STORE_NAME_LENGTH].rstrip()
    return "?"


@dataclass
class _Store:
    key: str
    tx_ids: list[uuid.UUID] = field(default_factory=list)
    total: Decimal = Decimal(0)
    categories: Counter[uuid.UUID | None] = field(default_factory=Counter)
    types: Counter[str] = field(default_factory=Counter)
    descriptions: Counter[str] = field(default_factory=Counter)


async def _visible_tree(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[list[CategoryGroup], list[Category]]:
    """Visible groups and the categories that can still be assigned, in the
    order the tree is shown: group position and name, then category name."""
    groups = [
        g for g in (
            await session.execute(
                select(CategoryGroup).where(CategoryGroup.workspace_id == workspace_id)
            )
        ).scalars().all()
        if not g.is_hidden
    ]
    groups.sort(key=lambda g: (g.position or 0, _norm(g.name), g.name, str(g.id)))
    order = {g.id: n for n, g in enumerate(groups)}
    categories = [
        c for c in (
            await session.execute(select(Category).where(Category.workspace_id == workspace_id))
        ).scalars().all()
        if not c.is_hidden and (c.group_id is None or c.group_id in order)
    ]
    categories.sort(
        key=lambda c: (
            order.get(c.group_id, len(order)) if c.group_id is not None else len(order),
            _norm(c.name), c.name, str(c.id),
        )
    )
    return groups, categories


async def collect_input(session: AsyncSession, workspace_id: uuid.UUID) -> OrganizerInput:
    """The tree and the last 12 months, summarized for Opus, plus the
    backend-only maps needed to apply its answer."""
    groups, categories = await _visible_tree(session, workspace_id)
    group_short = {g.id: f"g{n}" for n, g in enumerate(groups, start=1)}
    cat_short = {c.id: f"c{n}" for n, c in enumerate(categories, start=1)}

    since = app_today() - WINDOW
    rows = await session.execute(
        select(
            Transaction.id,
            Transaction.description,
            Transaction.amount,
            Transaction.type,
            Transaction.category_id,
            Payee.name.label("payee_name"),
            Payee.source.label("payee_source"),
        )
        .outerjoin(
            Payee, and_(Payee.id == Transaction.payee_id, Payee.workspace_id == workspace_id)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.status == "posted",
            Transaction.transfer_pair_id.is_(None),
            Transaction.is_ignored.is_(False),
            Transaction.source != "opening_balance",
            Transaction.date >= since,
        )
        .order_by(Transaction.date, Transaction.id)
    )
    per_category: dict[uuid.UUID, list[Decimal]] = {}
    stores: dict[str, _Store] = {}
    for row in rows.all():
        amount = abs(row.amount or Decimal(0))
        if row.category_id is not None:
            per_category.setdefault(row.category_id, []).append(amount)
        key = merchant_key(row.description, payee_name=row.payee_name, payee_source=row.payee_source)
        store = stores.setdefault(key, _Store(key=key))
        store.tx_ids.append(row.id)
        store.total += amount
        store.categories[row.category_id] += 1
        store.types[row.type] += 1
        store.descriptions[row.description or ""] += 1

    stats = OrganizerStats(groups={group_short[g.id]: g.name for g in groups}, categories={})

    def category_item(cat: Category) -> dict[str, Any]:
        amounts = per_category.get(cat.id, [])
        total = sum(amounts, Decimal(0))
        short = cat_short[cat.id]
        stats.categories[short] = CategoryStat(
            name=cat.name,
            group=group_short.get(cat.group_id) if cat.group_id is not None else None,
            tx=len(amounts), total=total, transfer=bool(cat.treat_as_transfer),
        )
        item: dict[str, Any] = {"c": short, "name": _one_line(cat.name), "q": len(amounts),
                                "v": _money(total)}
        if cat.treat_as_transfer:
            item["t"] = 1
        return item

    tree = [
        {
            "g": group_short[g.id],
            "name": _one_line(g.name),
            "cats": [category_item(c) for c in categories if c.group_id == g.id],
        }
        for g in groups
    ]
    ungrouped = [category_item(c) for c in categories if c.group_id is None]

    ranked = sorted(stores.values(), key=lambda s: (-s.total, -len(s.tx_ids), s.key))
    ranked = ranked[:STORE_LIMIT]
    remembered: dict[str, str | None] = {}
    if ranked:
        remembered = {
            row.merchant_key: row.ai_clean_name
            for row in (
                await session.execute(
                    select(MerchantMemory.merchant_key, MerchantMemory.ai_clean_name).where(
                        MerchantMemory.workspace_id == workspace_id,
                        MerchantMemory.merchant_key.in_([stored_key(s.key) for s in ranked]),
                    )
                )
            ).all()
        }
    store_items: list[dict[str, Any]] = []
    store_map: dict[str, dict[str, Any]] = {}
    for n, store in enumerate(ranked, start=1):
        short = f"i{n}"
        name = _store_name(store.key, remembered.get(stored_key(store.key)), store.descriptions)
        top_category = store.categories.most_common(1)[0][0]
        stats.stores[short] = StoreStat(name=name, tx=len(store.tx_ids), total=store.total)
        store_map[short] = {"key": store.key, "tx": [str(i) for i in store.tx_ids]}
        store_items.append({
            "k": short,
            "n": name,
            "c": cat_short.get(top_category) if top_category is not None else None,
            "q": len(store.tx_ids),
            "v": _money(store.total),
            "t": "c" if store.types.most_common(1)[0][0] == "credit" else "d",
        })

    text = json.dumps(
        {"groups": tree, "ungrouped": ungrouped, "stores": store_items},
        ensure_ascii=False, separators=(",", ":"),
    )
    ids = {short: str(gid) for gid, short in group_short.items()}
    ids.update({short: str(cid) for cid, short in cat_short.items()})
    return OrganizerInput(text=text, stats=stats, ids=ids, stores=store_map)


async def build_organizer_input(session: AsyncSession, workspace_id: uuid.UUID) -> str:
    """The user message: tree with short ids + per-store aggregates (12 months)."""
    return (await collect_input(session, workspace_id)).text


def organizer_params(system: list[dict[str, Any]], user: str) -> dict[str, Any]:
    """Keyword arguments for `client.beta.messages.create` (never a batch:
    the Batches API rejects `fallbacks`). Thinking cannot be disabled on
    Opus 5.5; effort is the control. No `temperature`."""
    return {
        "model": ORGANIZER_MODEL,
        "max_tokens": ORGANIZER_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": ORGANIZER_SCHEMA},
        },
        "betas": list(ORGANIZER_BETAS),
        "fallbacks": "default",
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }


# --- filtering the proposal --------------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _one_line(value)[:limit].rstrip()
    return cleaned or None


def _shape(raw: Any) -> dict[str, Any] | None:
    """The op as stored, or None when it is not one."""
    if not isinstance(raw, dict) or raw.get("op") not in OP_KINDS:
        return None
    op_id = _text(raw.get("id"), 16)
    if op_id is None:
        return None
    children_raw = raw.get("children")
    children = [
        c for c in (_text(item, NAME_LENGTH) for item in (children_raw or [])) if c is not None
    ] if isinstance(children_raw, list) else []
    return {
        "id": op_id,
        "op": raw["op"],
        "target": _text(raw.get("target"), NAME_LENGTH),
        "group": _text(raw.get("group"), NAME_LENGTH),
        "name": _text(raw.get("name"), NAME_LENGTH),
        "children": children,
        "into": _text(raw.get("into"), NAME_LENGTH),
        "why": _text(raw.get("why"), WHY_LENGTH) or "",
    }


_INVALID = object()


@dataclass
class _Sim:
    """The tree as the proposal leaves it, op after op."""

    cat_group: dict[str, str | None]
    cat_name: dict[str, str]
    transfer: dict[str, bool]
    group_name: dict[str, str]
    existing_groups: set[str]
    claimed: set[str] = field(default_factory=set)
    #: group key -> [(op position, +1 add / -1 remove / 0 create)]
    touches: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    @classmethod
    def start(cls, stats: OrganizerStats) -> _Sim:
        return cls(
            cat_group={s: c.group for s, c in stats.categories.items()},
            cat_name={s: _norm(c.name) for s, c in stats.categories.items()},
            transfer={s: c.transfer for s, c in stats.categories.items()},
            group_name={s: _norm(name) for s, name in stats.groups.items()},
            existing_groups=set(stats.groups),
        )

    def touch(self, group: str | None, position: int, delta: int) -> None:
        if group is not None:
            self.touches.setdefault(group, []).append((position, delta))

    def size(self, group: str) -> int:
        return sum(1 for g in self.cat_group.values() if g == group)

    def group_ref(self, ref: str | None) -> Any:
        """A group key (existing short id, or `new:<name>`), or `_INVALID`
        (missing, unknown id, or a category/store id: a third level)."""
        if ref is None:
            return _INVALID
        if _SHORT_RE.match(ref):
            return ref if ref.startswith("g") and ref in self.group_name else _INVALID
        wanted = _norm(ref)
        for key, name in self.group_name.items():
            if name == wanted:
                return key
        return f"new:{wanted}"

    def ensure_group(self, key: str, position: int) -> None:
        if key not in self.group_name:
            self.group_name[key] = key.removeprefix("new:")
            self.touch(key, position, 0)

    def has_category_named(self, group: str, name: str, *, besides: str | None = None) -> bool:
        wanted = _norm(name)
        return any(
            g == group and self.cat_name.get(c) == wanted and c != besides
            for c, g in self.cat_group.items()
        )


def _new_usage(stores: list[str], stats: OrganizerStats) -> tuple[int, Decimal]:
    tx = sum(stats.stores[s].tx for s in stores)
    total = sum((stats.stores[s].total for s in stores), Decimal(0))
    return tx, total


def _enough(tx: int, total: Decimal, times: int = 1) -> bool:
    return tx >= MIN_NEW_TX * times or total >= MIN_NEW_TOTAL * times


def _step(sim: _Sim, position: int, op: dict[str, Any], stats: OrganizerStats) -> bool:
    """Apply one op to the simulation; False (and nothing changed) when the
    op is invalid at this point of the proposal."""
    kind = op["op"]
    target, name = op["target"], op["name"]
    if kind == "create_group":
        if name is None or op["group"] is not None:  # a parent group: a third level
            return False
        key = sim.group_ref(name)
        if key is _INVALID or not str(key).startswith("new:") or key in sim.group_name:
            return False
        sim.ensure_group(key, position)
        return True

    if kind == "create_category":
        stores = op["children"]
        key = sim.group_ref(op["group"])
        if (
            name is None
            or key is _INVALID
            or not stores
            or len(set(stores)) != len(stores)
            or any(s not in stats.stores or s in sim.claimed for s in stores)
            or not _enough(*_new_usage(stores, stats))
            or (key in sim.group_name and sim.has_category_named(key, name))
        ):
            return False
        sim.ensure_group(key, position)
        new = f"new:{position}"
        sim.cat_group[new] = key
        sim.cat_name[new] = _norm(name)
        sim.transfer[new] = False
        sim.claimed.update(stores)
        sim.touch(key, position, +1)
        return True

    if kind == "rename":
        if name is None or target is None:
            return False
        if target in sim.cat_group and not target.startswith("new:"):
            group = sim.cat_group[target]
            if _norm(name) == sim.cat_name[target] or (
                group is not None and sim.has_category_named(group, name, besides=target)
            ):
                return False
            sim.cat_name[target] = _norm(name)
            return True
        if target in sim.existing_groups and target in sim.group_name:
            wanted = _norm(name)
            if any(n == wanted for n in sim.group_name.values()):
                return False
            sim.group_name[target] = wanted
            return True
        return False

    if kind == "move_category":
        if target is None or target not in sim.cat_group or not _SHORT_RE.match(target):
            return False
        key = sim.group_ref(op["group"])
        old = sim.cat_group[target]
        if key is _INVALID or key == old:
            return False
        sim.ensure_group(key, position)
        sim.cat_group[target] = key
        sim.touch(old, position, -1)
        sim.touch(key, position, +1)
        return True

    if kind == "merge":
        into = op["into"]
        if (
            target is None or into is None or target == into
            or target not in sim.cat_group or into not in sim.cat_group
            or not _SHORT_RE.match(target) or not _SHORT_RE.match(into)
            or sim.transfer[target] != sim.transfer[into]
        ):
            return False
        sim.touch(sim.cat_group.pop(target), position, -1)
        return True

    # split_category
    children = op["children"]
    if (
        target is None or name is None
        or target not in sim.cat_group or not _SHORT_RE.match(target)
        or sim.transfer[target]
        or not GROUP_MIN <= len(children) <= GROUP_MAX
        or len({_norm(c) for c in children}) != len(children)
    ):
        return False
    usage = stats.categories.get(target)
    if usage is None or not _enough(usage.tx, usage.total, len(children)):
        return False
    key = sim.group_ref(name)
    if key is _INVALID:
        return False
    sim.ensure_group(key, position)
    old = sim.cat_group[target]
    sim.cat_group[target] = None  # out of the way while the children are named
    sim.cat_name[target] = _norm(children[0])
    added = 1
    for n, child in enumerate(children[1:], start=1):
        if sim.has_category_named(key, child, besides=target):
            continue  # an existing category of that name is reused
        new = f"new:{position}:{n}"
        sim.cat_group[new] = key
        sim.cat_name[new] = _norm(child)
        sim.transfer[new] = False
        added += 1
    sim.cat_group[target] = key
    if old == key:
        sim.touch(key, position, added - 1)
    else:
        sim.touch(old, position, -1)
        sim.touch(key, position, added)
    return True


def _simulate(
    ops: list[dict[str, Any]], excluded: set[int], stats: OrganizerStats
) -> tuple[list[int], _Sim]:
    sim = _Sim.start(stats)
    kept: list[int] = []
    for position, op in enumerate(ops):
        if position not in excluded and _step(sim, position, op, stats):
            kept.append(position)
    return kept, sim


def _offender(sim: _Sim) -> int | None:
    """The op to drop for the first group the proposal leaves out of 2-6.

    Too big: the last op that added to it. Too small: the last op that took
    a category out of it, or (a new group nobody fills) the last op that
    touched it. An existing group emptied entirely is left alone.
    """
    for group, touches in sim.touches.items():
        size = sim.size(group)
        if size > GROUP_MAX:
            adds = [p for p, d in touches if d > 0]
            if adds:
                return max(adds)
        elif size < GROUP_MIN:
            if size == 0 and group in sim.existing_groups:
                continue
            removes = [p for p, d in touches if d < 0]
            return max(removes) if removes else max(p for p, _ in touches)
    return None


def _impact(op: dict[str, Any], stats: OrganizerStats) -> dict[str, Any]:
    kind, target = op["op"], op["target"]
    tx, total = 0, Decimal(0)
    if kind == "create_category":
        tx, total = _new_usage(op["children"], stats)
    elif target in stats.categories:
        tx, total = stats.categories[target].tx, stats.categories[target].total
    elif kind == "rename" and target in stats.groups:
        members = [c for c in stats.categories.values() if c.group == target]
        tx = sum(c.tx for c in members)
        total = sum((c.total for c in members), Decimal(0))
    return {"tx": tx, "total": float(total.quantize(_CENT, rounding=ROUND_HALF_UP))}


def _labels(op: dict[str, Any], stats: OrganizerStats) -> dict[str, Any]:
    """Readable names for the person (the short ids mean nothing to them)."""

    def name_of(short: str | None) -> str | None:
        if short is None:
            return None
        if short in stats.categories:
            return stats.categories[short].name
        return stats.groups.get(short, short)

    labels: dict[str, Any] = {
        "target": name_of(op["target"]),
        "group": name_of(op["group"]),
        "into": name_of(op["into"]),
    }
    if op["op"] == "create_category":
        labels["children"] = [stats.stores[s].name for s in op["children"]]
    else:
        labels["children"] = list(op["children"])
    return labels


def filter_ops(ops: list[Any], stats: OrganizerStats) -> list[dict[str, Any]]:
    """The ops worth showing, normalized, each `pending`.

    Dropped: malformed ops and repeated ids; ops naming ids that are not in
    the input (or that an earlier op merged away); any third level (a group
    under a group, a category under a category); a new category with fewer
    than `MIN_NEW_TX` transactions and less than `MIN_NEW_TOTAL` in its
    stores (a split needs that much per child); a merge between a transfer
    and a spending category; no-op renames and moves; and, op by op, what
    leaves a touched group outside `GROUP_MIN`..`GROUP_MAX` categories
    (`_offender`). Impact is recomputed from the real numbers.
    """
    shaped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ops:
        op = _shape(raw)
        if op is not None and op["id"] not in seen:
            seen.add(op["id"])
            shaped.append(op)

    excluded: set[int] = set()
    while True:
        kept, sim = _simulate(shaped, excluded, stats)
        offender = _offender(sim)
        if offender is None:
            break
        excluded.add(offender)

    result: list[dict[str, Any]] = []
    for position in kept:
        op = shaped[position]
        result.append({
            **op,
            "impact": _impact(op, stats),
            "labels": _labels(op, stats),
            "status": "pending",
        })
    return result


# --- the run ---------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _finish(run: CategorizationRun, status: str, error: str | None = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = _now()
    message = MESSAGES.get(error or status)
    if message is not None:
        run.detail = {**(run.detail or {}), "message": message}


def _account(run: CategorizationRun, message: Any) -> None:
    usage = getattr(message, "usage", None)

    def tokens(name: str) -> int:
        return int(getattr(usage, name, None) or 0)

    # A server-side fallback may have served the answer: price what served it
    # when known, the requested model otherwise.
    served = getattr(message, "model", None)
    model = served if isinstance(served, str) and served in PRICES else ORGANIZER_MODEL
    run.input_tokens = (run.input_tokens or 0) + tokens("input_tokens")
    run.output_tokens = (run.output_tokens or 0) + tokens("output_tokens")
    run.cache_write_tokens = (run.cache_write_tokens or 0) + tokens("cache_creation_input_tokens")
    run.cache_read_tokens = (run.cache_read_tokens or 0) + tokens("cache_read_input_tokens")
    run.cost_usd = Decimal(run.cost_usd or 0) + cost_usd(model, usage, batch=False)


def _answer(message: Any) -> list[Any] | None:
    """The answer's ops, read from its text block (thinking and fallback
    blocks come first and are skipped), or None when unreadable."""
    for block in getattr(message, "content", None) or []:
        text = getattr(block, "text", None)
        if getattr(block, "type", None) != "text" or not isinstance(text, str):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            return None
        ops = data.get("ops") if isinstance(data, dict) else None
        return ops if isinstance(ops, list) else None
    return None


async def organize(session: AsyncSession, workspace_id: uuid.UUID, run: CategorizationRun) -> None:
    """Execute a claimed (`running`) organize run: leaves it `done` with a
    proposal, `capped` or `failed` (with a friendly `detail["message"]`).

    API errors propagate (the task records them).
    """
    settings = await get_settings(session, workspace_id)
    client = await resolve_client(session, settings)
    if client is None:
        _finish(run, "failed", "no_ai_connection")
        return
    try:
        prepared = await collect_input(session, workspace_id)
        params = organizer_params(
            [{"type": "text", "text": ORGANIZER_INSTRUCTIONS}], prepared.text
        )
        spent = await classifier.committed_spend(session, workspace_id)
        if spent + classifier.estimate_cost(params, batch=False) > Decimal(settings.monthly_cap_usd):
            _finish(run, "capped")
            return
        run.model = ORGANIZER_MODEL
        message = await create_beta_message(client, params)
    finally:
        await client.close()
    _account(run, message)
    # The call is paid: its cost must reach the month's spend even if
    # reading the answer fails and the task rolls back.
    await session.commit()

    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        logger.warning("organizer run %s: the model declined", run.id)
        _finish(run, "failed", "refusal")
        return
    if stop == "max_tokens":
        _finish(run, "failed", "incomplete")
        return
    raw_ops = _answer(message)
    if raw_ops is None:
        _finish(run, "failed", "invalid_answer")
        return
    ops = filter_ops(raw_ops, prepared.stats)
    stores = {
        short: prepared.stores[short]
        for op in ops if op["op"] == "create_category"
        for short in op["children"]
    }
    run.detail = {**(run.detail or {}), "ops": ops, "ids": prepared.ids, "stores": stores}
    run.stats = {"proposed": len(raw_ops), "kept": len(ops)}
    _finish(run, "done")
    await sync_provider_map(session, workspace_id)


async def _family_candidates(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: uuid.UUID
) -> list[Candidate]:
    """Counted rows of the split category, whoever filed them, minus the
    ones already waiting on a question in Review."""
    waiting = exists().where(
        CategorizationSuggestion.transaction_id == Transaction.id,
        CategorizationSuggestion.status == "pending",
        blocking_suggestion(),
    )
    rows = await session.execute(
        select(
            Transaction.id, Transaction.description, Transaction.amount, Transaction.type,
            Transaction.date, Transaction.category_id, Transaction.category_source,
            Account.type.label("account_type"),
            Payee.name.label("payee_name"), Payee.source.label("payee_source"),
        )
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(
            Payee, and_(Payee.id == Transaction.payee_id, Payee.workspace_id == workspace_id)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.category_id == category_id,
            Transaction.status == "posted",
            Transaction.transfer_pair_id.is_(None),
            Transaction.is_ignored.is_(False),
            Transaction.source != "opening_balance",
            ~waiting,
        )
        .order_by(Transaction.date, Transaction.id)
    )
    return [
        Candidate(
            tx_id=row.id,
            key=merchant_key(row.description, payee_name=row.payee_name,
                             payee_source=row.payee_source),
            description=row.description,
            payee_name=row.payee_name,
            amount=row.amount,
            type=row.type,
            account_type=row.account_type,
            date=row.date,
            current_category_id=row.category_id,
            current_source=row.category_source,
        )
        for row in rows.all()
    ]


async def redistribute(
    session: AsyncSession, workspace_id: uuid.UUID, run: CategorizationRun
) -> None:
    """Execute a claimed `redistribute` run: the split category's rows are
    classified among the split's children only (`allowed_category_ids`),
    through the categorizer (`classifier.classify`, Messages or a Batch),
    `user` rows included, as `ai` with events under this run's id."""
    detail = run.detail or {}
    try:
        source = uuid.UUID(str(detail["source_category_id"]))
        family = {uuid.UUID(str(raw)) for raw in detail["allowed_category_ids"]}
    except (KeyError, TypeError, ValueError):
        _finish(run, "failed", "categories_gone")
        return
    family &= await category_service.get_assignable_category_ids(session, workspace_id)
    if source not in family or len(family) < 2:
        _finish(run, "failed", "categories_gone")
        return
    candidates = await _family_candidates(session, workspace_id, source)
    groups = group_by_key(candidates)
    run.stats = {
        "candidates": len(candidates), "groups": len(groups),
        "memory": 0, "applied": 0, "suggested": 0, "skipped": 0,
    }
    if not groups:
        _finish(run, "done")
        return
    client = await resolve_client(session, await get_settings(session, workspace_id))
    if client is None:
        _finish(run, "failed", "no_ai_connection")
        return
    try:
        await classifier.classify(
            session, workspace_id, groups, run, client=client, allowed_category_ids=family
        )
    finally:
        await client.close()
    if run.status == "running":
        run.status = "done"
    if run.status in ("done", "capped"):
        run.finished_at = _now()
    await session.flush()


# --- runs for the API ------------------------------------------------------------


async def expire_stale_runs(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Organizer runs a dead worker left busy stop blocking the workspace."""
    from app.tasks import categorization_tasks as tasks

    now = _now()
    for status, age in tasks.STALE_AFTER.items():
        await session.execute(
            update(CategorizationRun)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind.in_(ORGANIZER_KINDS),
                CategorizationRun.status == status,
                CategorizationRun.created_at < now - age,
            )
            .values(status="failed", error=ABANDONED_ERROR, finished_at=now)
            .execution_options(synchronize_session=False)
        )


async def request_run(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[CategorizationRun, bool]:
    """The busy organize run, or a new queued one; (run, created)."""
    from app.tasks import categorization_tasks as tasks

    await tasks.lock_workspace(session, workspace_id)
    await expire_stale_runs(session, workspace_id)
    busy = (
        await session.execute(
            select(CategorizationRun)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind == "organize",
                CategorizationRun.status.in_(BUSY_STATUSES),
            )
            .order_by(CategorizationRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if busy is not None:
        return busy, False
    run = CategorizationRun(
        id=uuid.uuid4(), workspace_id=workspace_id, kind="organize", trigger="manual",
        status="queued", stats={}, detail={}, created_at=_now(),
    )
    session.add(run)
    await session.flush()
    return run, True


async def latest_run(session: AsyncSession, workspace_id: uuid.UUID) -> CategorizationRun | None:
    return (
        await session.execute(
            select(CategorizationRun)
            .where(
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind == "organize",
            )
            .order_by(CategorizationRun.created_at.desc(), CategorizationRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def view(run: CategorizationRun | None) -> dict[str, Any]:
    """What the Organize tab shows: the run, its ops and its message (the
    backend-only maps stay out)."""
    if run is None:
        return {"run": None, "ops": [], "message": None}
    detail = run.detail or {}
    return {"run": run, "ops": detail.get("ops", []), "message": detail.get("message")}


# --- the Pluggy map --------------------------------------------------------------


async def sync_provider_map(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, str]:
    """Fill `provider_category_map` ({Pluggy target name -> category id}).

    An entry that still names a category of the workspace is kept (it is
    what lets a renamed category keep receiving Pluggy's categories); a
    missing or dangling one is resolved by the category's CURRENT name, as
    the sync's name match would. Called after an organize run and before
    every op, so a rename never loses the match.
    """
    from app.services.connection_service import PLUGGY_CATEGORY_MAP

    settings = await get_settings(session, workspace_id)
    current = {
        str(k): str(v) for k, v in (settings.provider_category_map or {}).items() if v
    }
    rows = (
        await session.execute(
            select(Category.id, Category.name, Category.is_hidden)
            .where(Category.workspace_id == workspace_id)
            .order_by(Category.is_hidden, Category.id)
        )
    ).all()
    ids = {str(row.id) for row in rows}
    by_name: dict[str, str] = {}
    for row in rows:
        by_name.setdefault(row.name, str(row.id))
    changed = False
    for name in sorted(set(PLUGGY_CATEGORY_MAP.values())):
        if current.get(name) in ids:
            continue
        match = by_name.get(name)
        if match is not None:
            current[name] = match
            changed = True
        elif name in current:
            del current[name]
            changed = True
    if changed or current != (settings.provider_category_map or {}):
        settings.provider_category_map = current
        await session.flush()
    return current


async def _repoint_provider_map(
    session: AsyncSession, workspace_id: uuid.UUID, old: uuid.UUID, new: uuid.UUID
) -> None:
    settings = await get_settings(session, workspace_id)
    mapping = dict(settings.provider_category_map or {})
    if str(old) in mapping.values():
        settings.provider_category_map = {
            k: (str(new) if v == str(old) else v) for k, v in mapping.items()
        }
        await session.flush()


# --- applying an op --------------------------------------------------------------


async def _locked_op(
    session: AsyncSession, workspace_id: uuid.UUID, run_id: uuid.UUID | None, op_id: str
) -> tuple[CategorizationRun, dict[str, Any]]:
    from app.tasks import categorization_tasks as tasks

    await tasks.lock_workspace(session, workspace_id)
    if run_id is None:
        latest = await latest_run(session, workspace_id)
        run_id = latest.id if latest is not None else None
    run = (
        await session.execute(
            select(CategorizationRun)
            .where(
                CategorizationRun.id == run_id,
                CategorizationRun.workspace_id == workspace_id,
                CategorizationRun.kind == "organize",
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none() if run_id is not None else None
    if run is None:
        raise NotFound("Nenhuma proposta do organizador.")
    op = next((o for o in (run.detail or {}).get("ops", []) if o.get("id") == op_id), None)
    if op is None:
        raise NotFound("Operação não encontrada.")
    if run.status != "done":
        raise OpConflict("A proposta ainda não está pronta.")
    if op.get("status") != "pending":
        raise OpConflict("Esta operação já foi decidida.")
    return run, dict(op)


def _save_op(run: CategorizationRun, op: dict[str, Any]) -> None:
    detail = dict(run.detail or {})
    detail["ops"] = [op if o.get("id") == op["id"] else o for o in detail.get("ops", [])]
    run.detail = detail


def _stored_id(run: CategorizationRun, short: str | None, prefix: str) -> uuid.UUID:
    raw = (run.detail or {}).get("ids", {}).get(short) if short else None
    if raw is None or not str(short).startswith(prefix):
        raise OpConflict("A operação cita algo que não existe mais.")
    return uuid.UUID(str(raw))


async def _category(
    session: AsyncSession, workspace_id: uuid.UUID, run: CategorizationRun, short: str | None
) -> Category:
    category = await category_service.get_category(
        session, _stored_id(run, short, "c"), workspace_id
    )
    if category is None:
        raise OpConflict("Uma categoria desta operação não existe mais.")
    return category


async def _group(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    run: CategorizationRun,
    ref: str | None,
) -> CategoryGroup:
    """An existing group by short id, else a visible group by name, else a new one."""
    if ref is None:
        raise OpConflict("A operação não diz em que grupo.")
    if _SHORT_RE.match(ref):
        group = await category_group_service.get_group(
            session, _stored_id(run, ref, "g"), workspace_id
        )
        if group is None:
            raise OpConflict("Um grupo desta operação não existe mais.")
        return group
    wanted = _norm(ref)
    for group in (
        await session.execute(
            select(CategoryGroup)
            .where(CategoryGroup.workspace_id == workspace_id)
            .order_by(CategoryGroup.position, CategoryGroup.id)
        )
    ).scalars().all():
        # A hidden group would hide what goes into it.
        if not group.is_hidden and _norm(group.name) == wanted:
            return group
    return await category_group_service.create_group(
        session, workspace_id, user_id, CategoryGroupCreate(name=ref[:NAME_LENGTH]), commit=False
    )


async def _category_in(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    group: CategoryGroup,
    name: str,
    *,
    besides: uuid.UUID | None = None,
) -> Category:
    """An assignable category of that name in the group, or a new one."""
    assignable = await category_service.get_assignable_category_ids(session, workspace_id)
    wanted = _norm(name)
    for category in (
        await session.execute(
            select(Category)
            .where(Category.workspace_id == workspace_id, Category.group_id == group.id)
            .order_by(Category.id)
        )
    ).scalars().all():
        if category.id != besides and category.id in assignable and _norm(category.name) == wanted:
            return category
    return await category_service.create_category(
        session, workspace_id, user_id,
        CategoryCreate(name=name[:NAME_LENGTH], group_id=group.id), commit=False,
    )


async def _move_stores(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run: CategorizationRun,
    op: dict[str, Any],
    category_id: uuid.UUID,
) -> int:
    """File the op's stores (the rows counted in its impact) under the new
    category, as `ai` with events under the organize run; their memory
    follows. Rows the person approved moving include their own."""
    stores = (run.detail or {}).get("stores", {})
    staged: list[StagedChange] = []
    reason = (op.get("why") or "organizador")[:REASON_LENGTH]
    for short in op.get("children", []):
        info = stores.get(short)
        if not isinstance(info, dict):
            continue
        tx_ids = [uuid.UUID(str(raw)) for raw in info.get("tx", [])]
        for start in range(0, len(tx_ids), classifier.ID_CHUNK):
            rows = (
                await session.execute(
                    select(Transaction)
                    .where(
                        Transaction.workspace_id == workspace_id,
                        Transaction.id.in_(tx_ids[start:start + classifier.ID_CHUNK]),
                    )
                    .with_for_update()
                )
            ).scalars().all()
            for tx in rows:
                if tx.transfer_pair_id is not None or tx.is_ignored:
                    continue
                change = stage_change(
                    tx, source="ai", new_category_id=category_id, run_id=run.id, reason=reason
                )
                if change is not None:
                    staged.append(change)
        key = info.get("key")
        if isinstance(key, str):
            await memory.repoint_store(session, workspace_id, stored_key(key), category_id)
    await write_staged(session, staged)
    return len(staged)


async def apply_op(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None,
    op_id: str,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Apply one pending op of an organize run (the latest when `run_id` is
    None); returns {"op": the op now `applied`, "redistribute_run_id"}.

    Nothing commits here: the caller commits (then enqueues the
    redistribution, if any) or rolls back. Raises NotFound or OpConflict.
    """
    run, op = await _locked_op(session, workspace_id, run_id, op_id)
    # Before anything moves, so a renamed category is still found by name.
    await sync_provider_map(session, workspace_id)
    kind = op["op"]
    result: dict[str, Any] = {}
    redistribute_run_id: uuid.UUID | None = None
    try:
        if kind == "create_group":
            group = await _group(session, workspace_id, user_id, run, op["name"])
            result["group_id"] = str(group.id)
        elif kind == "create_category":
            group = await _group(session, workspace_id, user_id, run, op["group"])
            category = await _category_in(session, workspace_id, user_id, group, op["name"] or "")
            result["category_id"] = str(category.id)
            result["moved"] = await _move_stores(session, workspace_id, run, op, category.id)
        elif kind == "rename":
            if str(op["target"]).startswith("g"):
                group_id = _stored_id(run, op["target"], "g")
                if await category_group_service.update_group(
                    session, group_id, workspace_id,
                    CategoryGroupUpdate(name=op["name"]), commit=False,
                ) is None:
                    raise OpConflict("Um grupo desta operação não existe mais.")
            else:
                category = await _category(session, workspace_id, run, op["target"])
                await category_service.update_category(
                    session, category.id, workspace_id, CategoryUpdate(name=op["name"]),
                    commit=False,
                )
        elif kind == "move_category":
            category = await _category(session, workspace_id, run, op["target"])
            group = await _group(session, workspace_id, user_id, run, op["group"])
            await category_service.update_category(
                session, category.id, workspace_id, CategoryUpdate(group_id=group.id),
                commit=False,
            )
        elif kind == "merge":
            target = await _category(session, workspace_id, run, op["target"])
            into = await _category(session, workspace_id, run, op["into"])
            await category_service.delete_category(
                session, target.id, workspace_id,
                transfer_to_id=into.id, user_id=user_id, commit=False,
            )
            await _repoint_provider_map(session, workspace_id, target.id, into.id)
        else:  # split_category
            target = await _category(session, workspace_id, run, op["target"])
            children = op["children"]
            if len(children) < GROUP_MIN:
                raise OpConflict("Uma divisão precisa de pelo menos duas categorias.")
            group = await _group(session, workspace_id, user_id, run, op["name"])
            await category_service.update_category(
                session, target.id, workspace_id,
                CategoryUpdate(name=children[0], group_id=group.id), commit=False,
            )
            family = [target.id]
            for name in children[1:]:
                child = await _category_in(
                    session, workspace_id, user_id, group, name, besides=target.id
                )
                if child.id not in family:
                    family.append(child.id)
            redistribution = CategorizationRun(
                id=uuid.uuid4(), workspace_id=workspace_id, kind="redistribute",
                trigger="manual", status="queued", stats={}, created_at=_now(),
                detail={
                    "source_category_id": str(target.id),
                    "allowed_category_ids": [str(i) for i in family],
                    "organize_run_id": str(run.id),
                    "op_id": op["id"],
                },
            )
            session.add(redistribution)
            redistribute_run_id = redistribution.id
            result.update(
                group_id=str(group.id),
                category_ids=[str(i) for i in family],
                redistribute_run_id=str(redistribution.id),
            )
    except (
        category_service.CategoryGroupNotFoundError,
        category_service.CategoryTransferError,
        category_service.CategoryInUseError,
        category_service.CategoryVisibilityError,
    ) as exc:
        raise OpConflict(str(exc)) from exc

    # The tree changed: decisions cached for the old tree are not trusted.
    await memory.forget_ai_decisions(session, workspace_id)
    op.update(status="applied", decided_at=_now().isoformat(), result=result)
    _save_op(run, op)
    await session.flush()
    return {"op": op, "redistribute_run_id": redistribute_run_id}


async def reject_op(
    session: AsyncSession, workspace_id: uuid.UUID, run_id: uuid.UUID | None, op_id: str
) -> dict[str, Any]:
    """Mark one pending op `rejected`; the caller commits."""
    run, op = await _locked_op(session, workspace_id, run_id, op_id)
    op.update(status="rejected", decided_at=_now().isoformat())
    _save_op(run, op)
    await session.flush()
    return op


__all__ = [
    "MESSAGES",
    "ORGANIZER_INSTRUCTIONS",
    "ORGANIZER_MODEL",
    "ORGANIZER_SCHEMA",
    "CategoryStat",
    "NotFound",
    "OpConflict",
    "OrganizerInput",
    "OrganizerStats",
    "StoreStat",
    "apply_op",
    "build_organizer_input",
    "collect_input",
    "filter_ops",
    "latest_run",
    "organize",
    "organizer_params",
    "redistribute",
    "reject_op",
    "request_run",
    "sync_provider_map",
    "view",
]
