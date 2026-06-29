"""
One-shot DB bootstrap: apply migrations, verify extensions, smoke the schema.

Usage:
  python -m scripts.bootstrap            # apply migrations + verify
  python -m scripts.bootstrap --ingest   # also run the geo spine (first data)

Reads the connection from config (DATABASE_URL or the POSTGRES_* fields). Safe to
re-run: migrations use IF NOT EXISTS. Works against local Docker or a managed
Postgres (Supabase / Neon).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from db.session import close_pool, get_conn

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"
REQUIRED_EXTENSIONS = ("postgis", "pg_trgm", "uuid-ossp")
EXPECTED_TABLES = ("nodes", "edges", "ingestion_runs", "resolution_candidates", "insights")


async def apply_migrations() -> None:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"No migrations found in {MIGRATIONS_DIR}", file=sys.stderr)
        return
    async with get_conn() as conn:
        for f in files:
            print(f"→ applying {f.name}")
            await conn.execute(f.read_text())
    print(f"✓ {len(files)} migration(s) applied")


async def verify() -> bool:
    ok = True
    async with get_conn() as conn:
        exts = {r["extname"] for r in await conn.fetch("SELECT extname FROM pg_extension")}
        for ext in REQUIRED_EXTENSIONS:
            present = ext in exts
            ok = ok and present
            print(f"  extension {ext:12} {'✓' if present else '✗ MISSING'}")
        tables = {
            r["tablename"]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        for t in EXPECTED_TABLES:
            present = t in tables
            ok = ok and present
            print(f"  table     {t:24} {'✓' if present else '✗ MISSING'}")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingest", action="store_true", help="run geo_spine after migrating")
    args = parser.parse_args()

    try:
        await apply_migrations()
        ok = await verify()
        if not ok:
            print("\nSchema incomplete — check that extensions are enabled "
                  "(Supabase: Database → Extensions).", file=sys.stderr)
            return 1
        if args.ingest:
            print("\n→ running geo spine …")
            from ingestion.flows import run_geo_spine

            await run_geo_spine()
            print("✓ geo spine ingested")
        print("\nBootstrap complete.")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
