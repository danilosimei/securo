#!/usr/bin/env python3
"""Re-key stores from the v1 merchant-key rule to v2, once, at deploy.

Run inside the backend container, after `alembic upgrade head`:
    docker exec <backend> python scripts/rekey_merchant_keys.py --dry-run
    docker exec <backend> python scripts/rekey_merchant_keys.py

Options:
    --dry-run         compute and print what would change; write nothing
    --workspace UUID  only this workspace

Idempotent: a second run changes nothing. A real run is refused (exit 2)
while any classification or redistribution is busy; the dry run still
reports it ("pulado: busy"). Close every browser tab of Securo first:
accepting, choosing or saving a note in the app does not take the
workspace lock, and one made during the real run can lose its pick.

The output starts with the stores whose picks fall below `MIN_CHOICES`
(they stop being filed by the memory alone until a new decision): read
them before the real run. What is merged and split is described in
`app/services/categorization/rekey.py`.
"""
import argparse
import asyncio
import os
import sys
import uuid
from typing import Any

# Allow running from repo root or backend root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.categorization.memory import MIN_CHOICES  # noqa: E402
from app.services.categorization.rekey import (  # noqa: E402
    MERGE,
    SPLIT,
    RekeyReport,
    busy_workspaces,
    rekey_all,
)

#: Biggest merges/splits listed per workspace.
TOP = 20


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regrava as chaves de loja da regra v1 para a v2 (idempotente)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="só mostra o que mudaria; não grava nada"
    )
    parser.add_argument("--workspace", type=uuid.UUID, help="só este workspace (UUID)")
    return parser.parse_args(argv)


def after_minus_before(recount: tuple[str, int | None, int]) -> int:
    _key, before, after = recount
    return after - (before or 0)


def below_min(report: RekeyReport) -> list[tuple[str, str, int]]:
    """Recomputed stores left below `MIN_CHOICES` picks that had at least
    that many: `(key, before, after)`, biggest losses first. A store created
    by a split had, before, the picks of the mixed key it came from."""
    split_from: dict[str, tuple[str, int]] = {}
    for change in report.changes:
        if change.kind != SPLIT:
            continue
        for key in change.new:
            if change.picks > split_from.get(key, ("", -1))[1]:
                split_from[key] = (change.old, change.picks)
    found: list[tuple[bool, int, str, str, int]] = []
    for key, before, after in report.recounts:
        if after >= MIN_CHOICES:
            continue
        if before is not None:
            if before >= MIN_CHOICES:
                found.append((False, after - before, key, str(before), after))
        elif key in split_from and split_from[key][1] >= MIN_CHOICES:
            source, picks = split_from[key]
            found.append((True, after - picks, key, f"nova ({source} tinha {picks})", after))
    found.sort()
    return [(key, before, after) for _new, _loss, key, before, after in found]


def format_below_min(reports: list[RekeyReport]) -> list[str]:
    rows = [
        (report.workspace_id, *row) for report in reports for row in below_min(report)
    ]
    if not rows:
        return [f"nenhuma loja fica abaixo de {MIN_CHOICES} escolhas"]
    lines = [
        f"ATENÇÃO: {len(rows)} lojas ficam abaixo de {MIN_CHOICES} escolhas e deixam de ser"
        " aplicadas sozinhas pela memória até uma nova decisão:"
    ]
    for ws_id, key, before, after in rows:
        lines.append(f"  {ws_id} {key}: {before} → {after} escolhas")
    return lines


def format_report(report: RekeyReport) -> list[str]:
    line = (
        f"{report.workspace_id} sugestões={report.suggestions} fundidas={report.merged}"
        f" divididas={report.split} semeadas={report.seeded}"
        f" propostas={report.proposals} marcadas={report.marked}"
        f" desmarcadas={report.unmarked}"
    )
    if report.skipped:
        line += f" [pulado: {report.skipped}"
        line += f" ({report.busy_run})]" if report.busy_run else "]"
    lines = [line]
    ranked = sorted(report.changes, key=lambda c: (-c.picks, c.old))
    for change in ranked[:TOP]:
        verb = "fundir " if change.kind == MERGE else "dividir"
        lines.append(
            f"  {verb} {change.old} ({change.picks} escolhas) → {', '.join(change.new)}"
        )
    if len(ranked) > TOP:
        lines.append(f"  … e mais {len(ranked) - TOP}")
    # Every recomputed store, biggest losses first: picks no row shows any
    # more are dropped by design, and this is where that shows.
    for key, before, after in sorted(
        report.recounts, key=lambda r: (after_minus_before(r), r[0])
    ):
        was = "nova" if before is None else str(before)
        lines.append(f"  recontar {key}: {was} → {after} escolhas")
    # Every dropped note, not only the top ones: the text exists nowhere else.
    for change in sorted(report.changes, key=lambda c: c.old):
        for note in change.notes:
            lines.append(f"  nota descartada de {change.old}: {note!r}")
    return lines


def _session_maker() -> tuple[Any, Any]:
    from app.core.database import async_session_maker, engine

    return async_session_maker, engine.dispose


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session_maker, dispose = _session_maker()
    try:
        busy = (
            [] if args.dry_run
            else await busy_workspaces(session_maker, workspace_id=args.workspace)
        )
        if busy:
            for ws_id, run in busy:
                print(f"recusado: {ws_id} {run}")
            print(
                "categorização em andamento: espere terminar, feche a aba do Securo"
                " e rode de novo (nada foi gravado)"
            )
            return 2
        reports = await rekey_all(
            session_maker, dry_run=args.dry_run, workspace_id=args.workspace
        )
    finally:
        await dispose()
    if args.dry_run:
        print("simulação (--dry-run): nada foi gravado")
    for line in format_below_min(reports):
        print(line)
    for report in reports:
        for line in format_report(report):
            print(line)
    skipped = sum(1 for r in reports if r.skipped)
    print(
        f"total: {len(reports)} workspaces, sugestões={sum(r.suggestions for r in reports)}"
        f" fundidas={sum(r.merged for r in reports)} divididas={sum(r.split for r in reports)}"
        f" semeadas={sum(r.seeded for r in reports)} propostas={sum(r.proposals for r in reports)}"
        f" marcadas={sum(r.marked for r in reports)}"
        f" desmarcadas={sum(r.unmarked for r in reports)} puladas={skipped}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    sys.exit(main())
