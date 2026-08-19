# Trident Oracle

An invoice intake and three-way match engine. Vendors send invoices as PDFs or phone photos;
the system extracts structured data, matches it against the corresponding Purchase Order and
Goods Receipt, flags discrepancies, and routes exceptions to a human approver via Telegram.
Clean, high-confidence, low-value invoices post automatically.

## Prerequisites

- Node.js 20+ with `pnpm` (via `corepack enable pnpm`)
- Python 3.11+ with [`uv`](https://docs.astral.sh/uv/)
- A Supabase project (Postgres, Storage)

## Setup

```bash
# Install JS workspace dependencies (apps/web)
pnpm install

# Install Python workspace dependencies (apps/api, apps/worker, packages/*)
uv sync --all-packages

# Copy env template and fill in real values
cp .env.example .env
```

## Running

```bash
# Frontend — Next.js dashboard
pnpm --filter web dev

# API — FastAPI
uv run --package api uvicorn api.main:app --reload

# Worker — poll loop
uv run --package worker python -m worker.main
```

## Testing

```bash
# Python
uv run pytest

# TypeScript
pnpm --filter web test
```

## Linting

```bash
uv run ruff check .
uv run mypy apps packages
pnpm --filter web lint
pnpm --filter web format
```
