#!/usr/bin/env python3
"""Fill `transactions.merchant_key` for existing rows, once, after the migration.

Run inside the backend container, after `alembic upgrade head`:
    docker exec <backend> python scripts/backfill_merchant_keys.py --dry-run
    docker exec <backend> python scripts/backfill_merchant_keys.py

Options:
    --dry-run         count what would change; write nothing
    --workspace UUID  only this workspace

Idempotent: a second run changes nothing. Run it again after renaming payees
or merging/deleting them in bulk: those edits do not refresh the stored key.
The rule itself is in `app/services/categorization/merchant_key.py`.
"""
import argparse
import asyncio
import os
import sys
import uuid
from typing import Any

# Allow running from repo root or backend root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.categorization.key_backfill import BackfillReport, backfill_all  # noqa: E402


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preenche a chave de loja gravada nas transações (idempotente)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="só conta o que mudaria; não grava nada"
    )
    parser.add_argument("--workspace", type=uuid.UUID, help="só este workspace (UUID)")
    return parser.parse_args(argv)


def format_report(report: BackfillReport) -> str:
    return (
        f"{report.workspace_id} lidas={report.scanned} preenchidas={report.filled}"
        f" corrigidas={report.corrected}"
    )


def _session_maker() -> tuple[Any, Any]:
    from app.core.database import async_session_maker, engine

    return async_session_maker, engine.dispose


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session_maker, dispose = _session_maker()
    try:
        reports = await backfill_all(
            session_maker, dry_run=args.dry_run, workspace_id=args.workspace
        )
    finally:
        await dispose()
    if args.dry_run:
        print("simulação (--dry-run): nada foi gravado")
    for report in reports:
        print(format_report(report))
    print(
        f"total: {len(reports)} workspaces, lidas={sum(r.scanned for r in reports)}"
        f" preenchidas={sum(r.filled for r in reports)}"
        f" corrigidas={sum(r.corrected for r in reports)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    sys.exit(main())
