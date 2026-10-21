#!/usr/bin/env python3
"""Re-key stores from the v1 merchant-key rule to v2, once, at deploy.

Run inside the backend container, after `alembic upgrade head`:
    docker exec <backend> python scripts/rekey_merchant_keys.py --dry-run
    docker exec <backend> python scripts/rekey_merchant_keys.py

Options:
    --dry-run         compute and print what would change; write nothing
    --workspace UUID  only this workspace

Idempotent: a second run changes nothing. A workspace with a busy
classification is skipped (reported "pulado: busy"); run again later.
What is merged and split is described in `app/services/categorization/rekey.py`.
"""
import argparse
import asyncio
import os
import sys
import uuid
from typing import Any

# Allow running from repo root or backend root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.categorization.rekey import (  # noqa: E402
    MERGE,
    RekeyReport,
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


def format_report(report: RekeyReport) -> list[str]:
    line = (
        f"{report.workspace_id} sugestões={report.suggestions} fundidas={report.merged}"
        f" divididas={report.split} semeadas={report.seeded}"
    )
    if report.skipped:
        line += f" [pulado: {report.skipped}]"
    lines = [line]
    ranked = sorted(report.changes, key=lambda c: (-c.picks, c.old))
    for change in ranked[:TOP]:
        verb = "fundir " if change.kind == MERGE else "dividir"
        extra = ", com nota" if change.had_note else ""
        lines.append(
            f"  {verb} {change.old} ({change.picks} escolhas{extra}) → {', '.join(change.new)}"
        )
    if len(ranked) > TOP:
        lines.append(f"  … e mais {len(ranked) - TOP}")
    return lines


def _session_maker() -> tuple[Any, Any]:
    from app.core.database import async_session_maker, engine

    return async_session_maker, engine.dispose


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session_maker, dispose = _session_maker()
    try:
        reports = await rekey_all(
            session_maker, dry_run=args.dry_run, workspace_id=args.workspace
        )
    finally:
        await dispose()
    if args.dry_run:
        print("simulação (--dry-run): nada foi gravado")
    for report in reports:
        for line in format_report(report):
            print(line)
    skipped = sum(1 for r in reports if r.skipped)
    print(
        f"total: {len(reports)} workspaces, sugestões={sum(r.suggestions for r in reports)}"
        f" fundidas={sum(r.merged for r in reports)} divididas={sum(r.split for r in reports)}"
        f" semeadas={sum(r.seeded for r in reports)} puladas={skipped}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    sys.exit(main())
