# seed_perf.py

Populates the database with realistic data for performance benchmarking and manual testing. Must be run inside the backend container since it imports app modules directly.

## Prerequisites

The stack must be running:

```bash
docker compose up -d
```

## Basic usage

```bash
# Full default seed (2024-01-01 through today)
docker compose exec backend python scripts/seed_perf.py

# Quick smoke run — 10% volume, same structure
docker compose exec backend python scripts/seed_perf.py --scale 0.1
```

## What gets seeded

At default settings the script creates the following data for one user:

| Entity | Default count | Controlled by |
|---|---|---|
| Accounts | 5 | `--accounts` |
| Categories | 30 (22 debit + 8 income) | `--categories` |
| Assets | 15 | `--assets` |
| Asset values | one per asset per day in range | `--start-date` |
| Transactions | 100,000 | `--scale` |
| Recurring transactions | 20 | `--scale` |
| FX rates (EUR + BRL) | one per day in range | `--start-date` |

Transactions are randomly distributed across the date range: 75% debit, 25% credit, with lognormal amounts.

## Options

| Flag | Default | Description |
|---|---|---|
| `--email` | `test@securo.app` | Email of the seed user |
| `--password` | `Securo123!` | Password of the seed user |
| `--start-date` | `2024-01-01` | Earliest date for all seeded data (format: `YYYY-MM-DD`) |
| `--scale` | `1.0` | Multiplier for transaction and recurring transaction counts |
| `--accounts` | `5` | Number of accounts (max 5) |
| `--categories` | `30` | Number of categories (max 30) |
| `--assets` | `15` | Number of assets (max 15) |
| `--no-reset` | _(unset)_ | Skip wiping existing data for the seed user before re-seeding |

### `--scale` in detail

`--scale` is a simple multiplier applied only to counts that can grow arbitrarily:

- Transactions: `int(100_000 × scale)`
- Recurring transactions: `int(20 × scale)`

It does **not** affect the number of accounts, categories, assets, or the date range. Use `--scale 0.1` for a fast smoke run and `--scale 2.0` to stress-test with 200k transactions.

### `--start-date` in detail

All time-series data (transactions, asset values, FX rates) spans from `--start-date` to the day the script runs. The default is `2024-01-01`. Use an earlier date to generate longer histories:

```bash
# 5 years of history
docker compose exec backend python scripts/seed_perf.py --start-date 2020-01-01
```

### `--no-reset`

By default the script wipes all existing data for the seed user before inserting. Pass `--no-reset` to skip the wipe — if the user already has transactions the script exits immediately without inserting anything.

## Examples

```bash
# Minimal dataset for a quick UI check
docker compose exec backend python scripts/seed_perf.py \
  --scale 0.1 --accounts 2 --categories 5 --assets 3

# Large dataset for performance profiling
docker compose exec backend python scripts/seed_perf.py \
  --scale 2.0 --start-date 2019-01-01

# Seed a second user without touching the default one
docker compose exec backend python scripts/seed_perf.py \
  --email analyst@securo.app --password Securo123!

# Re-seed only transactions (skip wipe, then add more — only works on a fresh user)
docker compose exec backend python scripts/seed_perf.py --no-reset
```

## Reproducibility

The script uses a fixed RNG seed (`random.Random(42)`), so the same arguments always produce the same data distribution. The only variable is `--start-date` relative to the current date, which determines the date range.

# rekey_merchant_keys.py

Rewrites the store keys (`merchant_key`) written under the v1 rule to the v2 rule (an acquirer prefix such as `Mlp*`, `Ebn *`, `Dl*` no longer becomes the store: `Mlp*Netshoes` is `NETSHOES`, not `MLP`). Runs **once per deploy that ships the v2 rule, after `alembic upgrade head`**, inside the backend container. Idempotent: running it again changes nothing.

```bash
# 1. See what would change (writes nothing)
docker exec <backend> python scripts/rekey_merchant_keys.py --dry-run

# 2. Apply
docker exec <backend> python scripts/rekey_merchant_keys.py

# Only one workspace
docker exec <backend> python scripts/rekey_merchant_keys.py --workspace <uuid>
```

What it does, per workspace (one transaction and one commit each), over the transactions the pipeline considers (posted, not a transfer pair, not an opening balance, not ignored):

- **Suggestions** (pending, accepted and dismissed, on any transaction) keyed with the old key of their transaction, with a key that no longer exists, or with a key the re-key touches, take the transaction's new key; one under another key (a payee's `P:...`) stays where its pick is.
- **Pending organizer proposals** (`organize` runs not yet decided) that name a store by a key that no longer exists take the key most of the store's rows now have.
- **Store memory**: a key that turns into one store is **merged** into it; a key that mixed several stores (like `MLP`) is **split**. Every key involved (the old one and all its new stores, and nothing else) is **recomputed from the person's own categorizations** of all its rows (ignored ones included): one pick per decision (rows filed together in one go count once; categories inherited by later parcels of a purchase and category merges are not new decisions), the latest decision as the last choice, exactly one event per decision marked as carrying its pick (so undoing a decision later takes back exactly that pick), and the cached AI decision dropped when the row changes. Picks no row shows any more are not kept. A merge keeps the newest note; a split never copies notes to the new stores (a note about the mixed store would mislead the AI). Every dropped note is printed with its text.
- While any workspace has a classification or redistribution in progress, the real run is **refused** before writing anything (`recusado: <id> <kind> <status> <run id>, criada há …`, exit code 2); the dry run still runs and shows that workspace as skipped (`[pulado: busy (…)]`). Run the script again once the run finishes, or once the beat expires an abandoned run. A run that starts between the check and the workspace lock still makes that workspace skipped.

Output: first, **every recomputed store that falls below 2 picks** while it had at least 2 (`ATENÇÃO: N lojas ficam abaixo de 2 escolhas …`, then `<workspace> SPOTIFY: 5 → 1 escolhas`, or `nova (MLP tinha 4) → 1` for a store split out of a mixed key; otherwise `nenhuma loja fica abaixo de 2 escolhas`). The recount is one pick per decision: rows filed in one go (a bulk edit) are one decision, so a store bulk-filed once drops to 1 pick and the memory stops filing it alone until the person decides again. Read this list before the real run. Then one line per workspace (`<id> sugestões=… fundidas=… divididas=… semeadas=… propostas=… marcadas=… desmarcadas=…`), followed by its 20 largest merges/splits (`fundir EBN (3 escolhas) → SPOTIFY`, `dividir MLP (5 escolhas) → KABUM, NETSHOES`), every recomputed store with its picks before and after (`recontar NETSHOES: 5 → 2 escolhas`, biggest losses first; picks no row shows any more are dropped), **every** dropped note with its text (`nota descartada de MLP: '…'`, keep the output to re-add it on the right store if needed), and a total.

**Close every browser tab of Securo before the real run** (and make sure no sync is about to start): accepting, choosing or saving a note in the app does not take the workspace lock, so one made while the script runs can land under a key that no longer exists and lose its pick. Take a database dump first: `alembic downgrade` does not bring the v1 keys back.

Details: `app/services/categorization/rekey.py`.
