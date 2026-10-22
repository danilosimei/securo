#!/usr/bin/env python3
"""Compare the previous categorization prompt with the new one on the person's own decisions.

Run inside the backend container (or any shell with the app's environment):

    python scripts/eval_categorization_prompt.py --workspace <UUID> \
        --descriptions descricoes.json --dry-run      # builds and prices, no call
    python scripts/eval_categorization_prompt.py --workspace <UUID> \
        --descriptions descricoes.json --days 60 --max-usd 0.50

Options:
    --workspace UUID      the workspace (optional when there is only one)
    --days N              the cut: decisions of the last N days are the truth (60)
    --descriptions FILE   JSON list of [group, category, description]; only the NEW prompt uses it
    --max-usd X           abort before calling when the estimate is above X (0.50)
    --dry-run             build both prompts and print the estimate; call nothing
    --include-settled     also ask about stores the memory already settled before the cut
                          (production would never send them to the model)

What it measures. The truth is what the person decided in the last N days: the
events with `source="user"` that carry a person (`user_id`), no run (`run_id`
is set by the organizer and by redistributions), and that the memory counted
as one deliberate pick (`memory_pick`: bulk edits, series follow-ups and
category merges are "derived" and never marked), not undone, with a category.
One per store: the most recent. Store = `Transaction.merchant_key`; `PIX:#...`
and `UNKNOWN` are left out (they never reach the prompt by name).

No leakage. Examples, corrections and the history `h` of both prompts are built
only from the same kind of event made BEFORE the cut (counts rebuilt per store
from those events, not from the current `MerchantMemory`). Store notes (`o`)
are left out: they carry no date. The OLD prompt is the previous format (tree
with names only, the 40 most picked stores overall, no corrections, without
the sentence about descriptions); the NEW is the one the classifier sends now.

Same items, same batches (`CHUNK_SIZE`), same model and client as the
classifier, one synchronous Messages call per batch. Nothing is written: the
session is rolled back, and no `CategorizationRun` is recorded (its `kind`
CHECK has no "eval" and adding one needs a migration), so the spend of this
one-off tool does not count toward the monthly cap -- the cost is printed.

Limits: the events are the only record of dated choices, so a pick made before
the events existed is not in the context; the history `h` ignores picks the
person took back.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# Allow running from repo root or backend root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.models.account import Account  # noqa: E402
from app.models.categorization import (  # noqa: E402
    CategorizationEvent,
    CategorizationSettings,
    MerchantMemory,
)
from app.models.category import Category  # noqa: E402
from app.models.category_group import CategoryGroup  # noqa: E402
from app.models.payee import Payee  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.services.categorization.claude_client import create_message, resolve_client  # noqa: E402
from app.services.categorization.classifier import (  # noqa: E402
    CHUNK_SIZE,
    _items,
    _sanitize,
    estimate_cost,
)
from app.services.categorization.corrections import recent_corrections  # noqa: E402
from app.services.categorization.costs import cost_usd  # noqa: E402
from app.services.categorization.memory import is_consistent  # noqa: E402
from app.services.categorization.merchant_key import (  # noqa: E402
    PAYEE_PREFIX,
    PERSON_PREFIX,
    is_unnamed_key,
)
from app.services.categorization.pipeline import (  # noqa: E402
    Candidate,
    MerchantGroup,
    categorizable,
    group_by_key,
)
from app.services.categorization.prompts import (  # noqa: E402
    CLASSIFIER_MODEL,
    INSTRUCTIONS,
    RULES_NOTE,
    CategoryIndex,
    build_category_index,
    build_corrections,
    build_examples,
    build_global_examples,
    build_history,
    build_result_schema,
    build_system_blocks,
    build_user_message,
    classify_params,
)

DEFAULT_DAYS = 60
DEFAULT_MAX_USD = Decimal("0.50")
#: Confidence bands, from the most to the least sure: (label, lowest value).
BANDS = (("90+", 90), ("80-89", 80), ("70-79", 70), ("<70", 0))
KINDS = ("descrição", "P:", "PIX:")


class CostAbort(Exception):
    """The estimate is above the limit: nothing was called."""


@dataclass(frozen=True)
class Decision:
    created_at: datetime
    key: str
    category_id: uuid.UUID


@dataclass(frozen=True)
class Prompt:
    """One variant: the cached system blocks and the ids it speaks."""

    name: str
    index: CategoryIndex
    system: list[dict[str, Any]]


@dataclass(frozen=True)
class Plan:
    params: dict[str, Any]
    key_by_item: dict[str, str]


@dataclass(frozen=True)
class Answer:
    category_id: uuid.UUID | None
    confidence: int


# --- the person's decisions ------------------------------------------------------------


async def load_decisions(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    since: datetime | None = None,
    before: datetime | None = None,
) -> list[Decision]:
    """The person's deliberate choices (see the module doc), oldest first."""
    query = (
        select(
            CategorizationEvent.created_at,
            Transaction.merchant_key,
            CategorizationEvent.new_category_id,
        )
        .join(Transaction, Transaction.id == CategorizationEvent.transaction_id)
        .where(
            CategorizationEvent.workspace_id == workspace_id,
            CategorizationEvent.source == "user",
            CategorizationEvent.user_id.is_not(None),
            CategorizationEvent.run_id.is_(None),
            CategorizationEvent.memory_pick.is_(True),
            CategorizationEvent.undone_at.is_(None),
            CategorizationEvent.new_category_id.is_not(None),
            Transaction.merchant_key.is_not(None),
        )
        .order_by(CategorizationEvent.created_at, CategorizationEvent.id)
    )
    if since is not None:
        query = query.where(CategorizationEvent.created_at >= since)
    if before is not None:
        query = query.where(CategorizationEvent.created_at < before)
    rows = (await session.execute(query)).all()
    return [
        Decision(created_at, key, category_id)
        for created_at, key, category_id in rows
        if not is_unnamed_key(key)
    ]


def latest_per_store(decisions: list[Decision]) -> dict[str, uuid.UUID]:
    """Store -> the category of its most recent decision."""
    latest: dict[str, Decision] = {}
    for decision in decisions:
        current = latest.get(decision.key)
        if current is None or decision.created_at >= current.created_at:
            latest[decision.key] = decision
    return {key: d.category_id for key, d in latest.items()}


def memory_before_cut(
    workspace_id: uuid.UUID, decisions: list[Decision]
) -> list[MerchantMemory]:
    """Transient memory rows (never added to a session) with the counts the
    decisions made before the cut give each store."""
    counts: dict[str, Counter[str]] = {}
    for decision in decisions:
        counts.setdefault(decision.key, Counter())[str(decision.category_id)] += 1
    return [
        MerchantMemory(workspace_id=workspace_id, merchant_key=key, counts=dict(picked))
        for key, picked in sorted(counts.items())
    ]


def key_kind(key: str) -> str:
    if key.startswith(PERSON_PREFIX):
        return "PIX:"
    if key.startswith(PAYEE_PREFIX):
        return "P:"
    return "descrição"


# --- the two prompts -------------------------------------------------------------------


def load_descriptions(path: Path) -> dict[tuple[str, str], str]:
    """`[group, category, description]` entries as (group, category) -> text,
    names compared case-insensitively."""
    data = json.loads(path.read_text(encoding="utf8"))
    return {
        (str(group).casefold(), str(name).casefold()): str(text)
        for group, name, text in data
        if str(text).strip()
    }


def tree_copy(
    categories: list[Category],
    groups: list[CategoryGroup],
    descriptions: dict[tuple[str, str], str] | None = None,
) -> tuple[list[Category], list[CategoryGroup], set[tuple[str, str]]]:
    """Transient copies of the tree (nothing the session tracks), with the
    descriptions written onto the copies. Returns the descriptions that matched
    no category too, so the caller can warn."""
    group_names = {g.id: g.name for g in groups}
    wanted = dict(descriptions or {})
    copied: list[Category] = []
    for cat in categories:
        group_name = group_names.get(cat.group_id, "") if cat.group_id else ""
        text = wanted.pop((group_name.casefold(), cat.name.casefold()), None)
        copied.append(
            Category(
                id=cat.id, name=cat.name, group_id=cat.group_id, is_hidden=cat.is_hidden,
                treat_as_transfer=cat.treat_as_transfer, description=text,
            )
        )
    copied_groups = [
        CategoryGroup(id=g.id, name=g.name, position=g.position, is_hidden=g.is_hidden)
        for g in groups
    ]
    return copied, copied_groups, set(wanted)


def old_context(index: CategoryIndex, examples: str) -> str:
    """The cached block as it was before descriptions and corrections."""
    return (
        "Categorias desta pessoa (id Grupo › Categoria):\n"
        f"{index.render() or '(nenhuma)'}\n\n"
        "Exemplos desta pessoa (loja -> id), dos mais usados para os menos usados:\n"
        f"{examples or '(nenhum ainda)'}"
    )


def build_prompts(
    categories: list[Category],
    groups: list[CategoryGroup],
    descriptions: dict[tuple[str, str], str],
    memory: list[MerchantMemory],
    corrections: list[tuple[str, uuid.UUID, uuid.UUID]],
) -> tuple[Prompt, Prompt, set[tuple[str, str]]]:
    """(old, new, unmatched descriptions). Both are built from the same
    `memory` and `corrections`, which must hold only what came before the cut."""
    plain, plain_groups, _ = tree_copy(categories, groups)
    old_index = build_category_index(plain, plain_groups)
    described, described_groups, unmatched = tree_copy(categories, groups, descriptions)
    new_index = build_category_index(described, described_groups)

    old_instructions = INSTRUCTIONS.replace(RULES_NOTE, "")
    old_system = [
        {"type": "text", "text": old_instructions, "cache_control": {"type": "ephemeral"}},
        {
            "type": "text",
            "text": old_context(old_index, build_global_examples(memory, old_index)),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    new_system = build_system_blocks(
        new_index,
        build_examples(memory, new_index),
        build_corrections(corrections, new_index),
    )
    return Prompt("antigo", old_index, old_system), Prompt("novo", new_index, new_system), unmatched


def plan_batches(
    prompt: Prompt,
    groups: list[MerchantGroup],
    history_rows: list[MerchantMemory],
) -> list[Plan]:
    """The classifier's batches: sorted by key, `CHUNK_SIZE` stores each."""
    schema = build_result_schema(prompt.index)
    history = build_history(history_rows, prompt.index)
    ordered = sorted(groups, key=lambda g: g.key)
    plans: list[Plan] = []
    for start in range(0, len(ordered), CHUNK_SIZE):
        chunk = ordered[start:start + CHUNK_SIZE]
        text, mapping = build_user_message(chunk, history, None)
        plans.append(
            Plan(classify_params(prompt.system, text, len(chunk), schema), mapping)
        )
    return plans


def estimate(plans: list[Plan]) -> Decimal:
    return sum((estimate_cost(plan.params, batch=False) for plan in plans), Decimal(0))


# --- calling the model and scoring -----------------------------------------------------


async def ask(client: Any, prompt: Prompt, plans: list[Plan]) -> tuple[dict[str, Answer], Decimal]:
    """Answers per store key and the cost actually billed."""
    answers: dict[str, Answer] = {}
    spent = Decimal(0)
    assignable = {cid for _, cid, _ in prompt.index.entries}
    for plan in plans:
        message = await create_message(client, plan.params)
        spent += cost_usd(CLASSIFIER_MODEL, getattr(message, "usage", None), batch=False)
        for item in _items(message) or []:
            parsed = _sanitize(item, prompt.index, assignable)
            if parsed is None or parsed[0] not in plan.key_by_item:
                continue
            item_id, answer = parsed
            answers.setdefault(
                plan.key_by_item[item_id], Answer(answer.category_id, answer.confidence)
            )
    return answers, spent


def band_of(confidence: int) -> str:
    return next(label for label, low in BANDS if confidence >= low)


@dataclass
class Tally:
    total: int = 0
    right: int = 0

    def add(self, hit: bool) -> None:
        self.total += 1
        self.right += int(hit)

    def text(self) -> str:
        if not self.total:
            return "-"
        return f"{100 * self.right / self.total:5.1f}% ({self.right}/{self.total})"


def score(
    truth: dict[str, uuid.UUID], answers: dict[str, Answer]
) -> tuple[Tally, dict[str, Tally], dict[str, Tally]]:
    """Hits over the truth's stores: total, by key kind, by confidence band.
    A store the model skipped, or answered with no category, is a miss (band
    `<70`: no confidence to speak of)."""
    total = Tally()
    by_kind = {kind: Tally() for kind in KINDS}
    by_band = {label: Tally() for label, _ in BANDS}
    for key, wanted in truth.items():
        answer = answers.get(key)
        hit = answer is not None and answer.category_id == wanted
        total.add(hit)
        by_kind[key_kind(key)].add(hit)
        by_band[band_of(answer.confidence if answer else 0)].add(hit)
    return total, by_kind, by_band


def format_report(
    truth: dict[str, uuid.UUID],
    old: dict[str, Answer],
    new: dict[str, Answer],
) -> str:
    o_total, o_kind, o_band = score(truth, old)
    n_total, n_kind, n_band = score(truth, new)
    width = 22
    lines = [f"{'':{width}}{'antigo':>20}{'novo':>20}"]
    lines.append(f"{'Total':{width}}{o_total.text():>20}{n_total.text():>20}")
    lines.append("Por tipo de chave")
    for kind in KINDS:
        lines.append(f"  {kind:{width - 2}}{o_kind[kind].text():>20}{n_kind[kind].text():>20}")
    lines.append("Por faixa de confiança (a de cada prompt)")
    for label, _ in BANDS:
        lines.append(f"  {label:{width - 2}}{o_band[label].text():>20}{n_band[label].text():>20}")
    return "\n".join(lines)


# --- the evaluation --------------------------------------------------------------------


@dataclass
class Evaluation:
    truth: dict[str, uuid.UUID]
    old_answers: dict[str, Answer]
    new_answers: dict[str, Answer]
    estimate: Decimal
    spent: Decimal
    settled: int
    unmatched: set[tuple[str, str]]


async def load_stores(
    session: AsyncSession, workspace_id: uuid.UUID, keys: set[str]
) -> dict[str, MerchantGroup]:
    """The prompt's view of each store: the pipeline's fields over all its
    posted transactions (descriptions and amounts say nothing about the label)."""
    if not keys:
        return {}
    candidates: list[Candidate] = []
    ordered = sorted(keys)
    for start in range(0, len(ordered), 500):
        rows = await session.execute(
            select(
                Transaction.id,
                Transaction.merchant_key,
                Transaction.description,
                Transaction.amount,
                Transaction.type,
                Transaction.date,
                Account.type.label("account_type"),
                Payee.name.label("payee_name"),
            )
            .join(Account, Account.id == Transaction.account_id)
            .outerjoin(
                Payee, (Payee.id == Transaction.payee_id) & (Payee.workspace_id == workspace_id)
            )
            .where(*categorizable(workspace_id), Transaction.merchant_key.in_(ordered[start:start + 500]))
            .order_by(Transaction.date, Transaction.id)
        )
        for row in rows.all():
            candidates.append(
                Candidate(
                    tx_id=row.id, key=row.merchant_key, description=row.description,
                    payee_name=row.payee_name, amount=row.amount, type=row.type,
                    account_type=row.account_type, date=row.date,
                    current_category_id=None,
                )
            )
    return group_by_key(candidates)


async def evaluate(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    client: Any | None,
    now: datetime,
    days: int = DEFAULT_DAYS,
    descriptions: dict[tuple[str, str], str] | None = None,
    max_usd: Decimal = DEFAULT_MAX_USD,
    dry_run: bool = False,
    include_settled: bool = False,
) -> Evaluation:
    cutoff = now - timedelta(days=days)
    categories = list(
        (await session.execute(select(Category).where(Category.workspace_id == workspace_id)))
        .scalars().all()
    )
    groups = list(
        (
            await session.execute(
                select(CategoryGroup).where(CategoryGroup.workspace_id == workspace_id)
            )
        ).scalars().all()
    )

    before = await load_decisions(session, workspace_id, before=cutoff)
    memory = memory_before_cut(workspace_id, before)
    corrections = await recent_corrections(session, workspace_id, before=cutoff)
    old_prompt, new_prompt, unmatched = build_prompts(
        categories, groups, descriptions or {}, memory, corrections
    )

    assignable = {cid for _, cid, _ in new_prompt.index.entries}
    truth = {
        key: category_id
        for key, category_id in latest_per_store(
            await load_decisions(session, workspace_id, since=cutoff)
        ).items()
        if category_id in assignable
    }
    settled_keys = {
        row.merchant_key for row in memory if is_consistent(dict(row.counts or {}))[1]
    }
    settled = len(set(truth) & settled_keys)
    if not include_settled:
        truth = {key: cat for key, cat in truth.items() if key not in settled_keys}

    stores = await load_stores(session, workspace_id, set(truth))
    truth = {key: cat for key, cat in truth.items() if key in stores}
    groups_to_ask = [stores[key] for key in truth]
    # The classifier sends `h` only for stores whose picks are mixed.
    mixed = [
        row for row in memory
        if row.merchant_key in truth and row.merchant_key not in settled_keys
    ]
    old_plans = plan_batches(old_prompt, groups_to_ask, mixed)
    new_plans = plan_batches(new_prompt, groups_to_ask, mixed)
    total_estimate = estimate(old_plans) + estimate(new_plans)
    if total_estimate > max_usd:
        raise CostAbort(
            f"estimativa US$ {total_estimate:.4f} acima do limite de US$ {max_usd:.2f}; "
            "nada foi chamado"
        )
    if dry_run or client is None or not truth:
        return Evaluation(truth, {}, {}, total_estimate, Decimal(0), settled, unmatched)

    old_answers, old_spent = await ask(client, old_prompt, old_plans)
    new_answers, new_spent = await ask(client, new_prompt, new_plans)
    return Evaluation(
        truth, old_answers, new_answers, total_estimate, old_spent + new_spent, settled, unmatched
    )


# --- command line ----------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compara o prompt antigo e o novo da categorização nas decisões da pessoa."
    )
    parser.add_argument("--workspace", type=uuid.UUID, help="workspace (UUID); opcional se houver um só")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="corte em dias (60)")
    parser.add_argument("--descriptions", type=Path, help="JSON [grupo, categoria, descrição]")
    parser.add_argument("--max-usd", type=Decimal, default=DEFAULT_MAX_USD, help="teto da estimativa (0.50)")
    parser.add_argument("--dry-run", action="store_true", help="monta e estima; não chama o modelo")
    parser.add_argument(
        "--include-settled", action="store_true",
        help="inclui lojas que a memória já resolvia antes do corte",
    )
    return parser.parse_args(argv)


async def _workspace_id(session: AsyncSession, wanted: uuid.UUID | None) -> uuid.UUID:
    if wanted is not None:
        return wanted
    ids = (await session.execute(select(CategorizationSettings.workspace_id))).scalars().all()
    if len(ids) != 1:
        raise SystemExit("informe --workspace: há mais de um (ou nenhum) workspace configurado")
    return ids[0]


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from app.core.database import async_session_maker, engine

    descriptions = load_descriptions(args.descriptions) if args.descriptions else {}
    try:
        async with async_session_maker() as session:
            try:
                workspace_id = await _workspace_id(session, args.workspace)
                client = None
                if not args.dry_run:
                    settings = await session.scalar(
                        select(CategorizationSettings).where(
                            CategorizationSettings.workspace_id == workspace_id
                        )
                    )
                    client = await resolve_client(session, settings) if settings else None
                    if client is None:
                        print("sem conexão de IA usável neste workspace", file=sys.stderr)
                        return 1
                try:
                    result = await evaluate(
                        session, workspace_id, client=client, now=datetime.now(timezone.utc),
                        days=args.days, descriptions=descriptions, max_usd=args.max_usd,
                        dry_run=args.dry_run, include_settled=args.include_settled,
                    )
                finally:
                    if client is not None:
                        await client.close()
            finally:
                await session.rollback()  # read-only: nothing is ever kept
    except CostAbort as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        await engine.dispose()

    print(
        f"lojas avaliadas: {len(result.truth)} (já resolvidas pela memória antes do corte: "
        f"{result.settled}{'' if args.include_settled else ', fora'})"
    )
    for group, name in sorted(result.unmatched):
        print(f"descrição sem categoria correspondente: {group} / {name}")
    print(f"estimativa: US$ {result.estimate:.4f}")
    if args.dry_run:
        print("simulação (--dry-run): nada foi chamado nem gravado")
        return 0
    print(f"gasto real: US$ {result.spent:.4f} (não entra no teto mensal)")
    print(format_report(result.truth, result.old_answers, result.new_answers))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    sys.exit(main())
