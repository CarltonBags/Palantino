"""
Async Postgres connection pool (asyncpg).

The whole data layer — writer, flows, resolver, text linker, scanner, API — is
written in the asyncpg dialect: `$1` placeholders, `conn.fetch / fetchrow /
execute`, and execute() returning a status string. So the pool is asyncpg, not
psycopg.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg

from config import settings

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Decode JSON/JSONB to Python objects on read and encode dicts on write, so
    # callers get/give dicts (not raw strings) for `properties`, geometry, etc.
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        common = dict(
            min_size=2,
            max_size=10,
            init=_init_conn,
            # 0 disables the prepared-statement cache — required behind a
            # transaction pooler (Supabase :6543, Neon pooled, PgBouncer).
            statement_cache_size=settings.db_statement_cache_size,
        )
        if settings.database_url:
            # Cloud Postgres via a single DSN URL (Supabase / Neon / RDS …).
            _pool = await asyncpg.create_pool(dsn=settings.database_url, **common)
        else:
            _pool = await asyncpg.create_pool(
                host=settings.postgres_host,
                port=settings.postgres_port,
                user=settings.postgres_user,
                password=settings.postgres_password,
                database=settings.postgres_db,
                **common,
            )
    return _pool


@asynccontextmanager
async def get_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
