from contextlib import asynccontextmanager
from typing import AsyncGenerator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from civic_graph.config import settings

_pool: AsyncConnectionPool | None = None


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.postgres_dsn,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row},
        )
        await _pool.open()
    return _pool


@asynccontextmanager
async def get_conn() -> AsyncGenerator[psycopg.AsyncConnection, None]:
    pool = await get_pool()
    async with pool.connection() as conn:
        yield conn


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
