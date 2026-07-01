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


async def _run_all() -> None:
    """
    Run connectors in dependency order, isolating failures so one bad source
    doesn't halt the rest. Geo spine first (everything snaps to it), then the
    data layers, then resolution. The heaviest feeds (gtfs_static ~144MB, the
    insight scan which needs ANTHROPIC_API_KEY) are left out of --all; run them
    on their own when wanted.
    """
    from ingestion import flows

    sequence = [
        ("geo_spine", flows.run_geo_spine),
        ("strassen", flows.run_strassen),
        ("strassenabschnitte", flows.run_strassenabschnitte),
        ("ods_pois", flows.run_ods_pois),
        ("ods_stats", flows.run_ods_stats),
        ("baustellen", flows.run_baustellen),
        ("brightsky", flows.run_brightsky),
        ("lanuv_air", flows.run_lanuv_air),
        ("polizei_rss", flows.run_polizei_rss),
        ("nordstadtblogger", flows.run_nordstadtblogger),
        ("wirindortmund", flows.run_wirindortmund),
        ("vergabe_nrw", flows.run_vergabe_nrw),
        ("wahlergebnisse", flows.run_wahlergebnisse),
        ("wahlergebnisse_stimmbezirk", flows.run_wahlergebnisse_stimmbezirk),
        ("dortmund_events", flows.run_dortmund_events),
        ("autobahn", flows.run_autobahn),
        ("gtfs_realtime", flows.run_gtfs_realtime),
        ("overpass", flows.run_overpass),
        ("ssb_dortmund", flows.run_ssb_dortmund),
        ("offeneregister", flows.run_offeneregister),
        ("tiefbau_programm", flows.run_tiefbau_programm),
        ("sozialindikatoren", flows.run_sozialindikatoren),
        ("haushalt", flows.run_haushalt),
        ("sports_fixtures", flows.run_sports_fixtures),
        ("gremientermine", flows.run_gremientermine),
        ("gremienniederschriften", flows.run_gremienniederschriften),
        ("text_linking", flows.run_text_linking),
        ("reference_linking", flows.run_reference_linking),
        ("entity_resolution", flows.run_resolution),
        ("embed_nodes", flows.run_embed_nodes),
    ]
    results: list[tuple[str, str]] = []
    for name, fn in sequence:
        print(f"\n→ {name} …")
        try:
            await fn()
            results.append((name, "ok"))
        except Exception as exc:  # noqa: BLE001 — isolate per connector
            print(f"  ✗ {name}: {exc}", file=sys.stderr)
            results.append((name, f"FAILED: {str(exc)[:120]}"))
    print("\n── ingest summary ──")
    for name, status in results:
        print(f"  {'✓' if status == 'ok' else '✗'} {name:28} {status}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingest", action="store_true", help="run geo_spine after migrating")
    parser.add_argument("--all", action="store_true", help="run all connectors in order")
    args = parser.parse_args()

    try:
        await apply_migrations()
        ok = await verify()
        if not ok:
            print("\nSchema incomplete — check that extensions are enabled "
                  "(Supabase: Database → Extensions).", file=sys.stderr)
            return 1
        if args.all:
            await _run_all()
        elif args.ingest:
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
