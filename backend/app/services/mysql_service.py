"""MySQL datasource service with per-user permission enforcement."""
import asyncio
import logging
import time
from decimal import Decimal
from datetime import date, datetime
from typing import Any
import aiomysql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.datasource import Datasource, UserDatasourcePermission
from app.models.user import User
from app.utils.security import decrypt_secret

logger = logging.getLogger(__name__)

def _serialize_row(row: dict) -> dict:
    """Convert non-JSON-serializable MySQL types to plain Python types."""
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# Simple connection pool cache
_pools: dict[str, aiomysql.Pool] = {}
_pool_lock = asyncio.Lock()


async def _get_pool(datasource: Datasource) -> aiomysql.Pool:
    key = str(datasource.id)
    if key not in _pools:
        async with _pool_lock:
            if key not in _pools:
                password = decrypt_secret(datasource.password_encrypted)
                pool = await aiomysql.create_pool(
                    host=datasource.host,
                    port=datasource.port,
                    user=datasource.username,
                    password=password,
                    db=datasource.database_name,
                    minsize=1,
                    maxsize=5,
                    autocommit=True,
                )
                _pools[key] = pool
    return _pools[key]


async def _get_datasource_with_permission(
    datasource_id: str,
    user: User,
    db: AsyncSession,
) -> tuple[Datasource, UserDatasourcePermission | None]:
    result = await db.execute(
        select(Datasource).where(Datasource.id == datasource_id, Datasource.is_active == True)
    )
    datasource = result.scalar_one_or_none()
    if not datasource:
        raise ValueError(f"Datasource {datasource_id} not found")

    if user.role == "admin":
        return datasource, None

    perm_result = await db.execute(
        select(UserDatasourcePermission).where(
            UserDatasourcePermission.user_id == user.id,
            UserDatasourcePermission.datasource_id == datasource_id,
        )
    )
    perm = perm_result.scalar_one_or_none()
    if not perm:
        raise PermissionError(f"No permission to access datasource {datasource_id}")
    return datasource, perm


def _check_sql_safety(sql: str) -> None:
    """SQL safety check - only allow SELECT/SHOW/DESCRIBE statements."""
    import re
    normalized = sql.strip().upper()
    if not re.match(r'^(SELECT|SHOW|DESCRIBE)\b', normalized):
        raise PermissionError("Only SELECT, SHOW, and DESCRIBE statements are allowed")
    dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "EXEC", "EXECUTE", "CALL", "LOAD", "OUTFILE"]
    for keyword in dangerous:
        if re.search(rf'\b{keyword}\b', normalized):
            raise PermissionError(f"Dangerous SQL keyword detected: {keyword}")


async def execute_query(
    datasource_id: str,
    sql: str,
    user: User,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    datasource, perm = await _get_datasource_with_permission(datasource_id, user, db)
    _check_sql_safety(sql)

    sql_preview = sql.strip().replace("\n", " ")[:120]
    logger.info("db_query | ds=%s user=%s sql=%r", datasource.name, user.username, sql_preview)

    t = time.monotonic()
    pool = await _get_pool(datasource)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(sql)
            rows = await cursor.fetchmany(500)  # Cap at 500 rows
            result = [_serialize_row(row) for row in rows]

    elapsed_ms = int((time.monotonic() - t) * 1000)
    logger.info("db_result | ds=%s rows=%d elapsed=%dms", datasource.name, len(result), elapsed_ms)
    return result


async def list_tables(
    datasource_id: str,
    user: User,
    db: AsyncSession,
) -> list[str]:
    datasource, perm = await _get_datasource_with_permission(datasource_id, user, db)

    pool = await _get_pool(datasource)
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SHOW TABLES")
            rows = await cursor.fetchall()
            all_tables = [row[0] for row in rows]

    if perm and perm.allowed_tables:
        return [t for t in all_tables if t in perm.allowed_tables]
    return all_tables


async def get_table_schema(
    datasource_id: str,
    table_name: str,
    user: User,
    db: AsyncSession,
) -> list[dict]:
    import re
    if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
        raise ValueError("Invalid table name")
    datasource, perm = await _get_datasource_with_permission(datasource_id, user, db)
    if perm and perm.allowed_tables and table_name not in perm.allowed_tables:
        raise PermissionError(f"No permission to access table {table_name}")

    pool = await _get_pool(datasource)
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(f"DESCRIBE `{table_name}`")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def fetch_full_schema(datasource: Datasource) -> dict:
    """Fetch full schema (tables + columns) from MySQL. Admin-only, no permission filter.
    Returns a dict suitable for storing as datasource.schema_cache."""
    import re
    from datetime import datetime, timezone

    pool = await _get_pool(datasource)
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute("SHOW TABLES")
            rows = await cursor.fetchall()
            tables = [row[0] for row in rows]

        columns: dict[str, list[dict]] = {}
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            for table in tables:
                if not re.match(r'^[a-zA-Z0-9_]+$', table):
                    continue
                await cursor.execute(f"DESCRIBE `{table}`")
                col_rows = await cursor.fetchall()
                columns[table] = [
                    {"name": r["Field"], "type": r["Type"], "nullable": r["Null"] == "YES"}
                    for r in col_rows
                ]

    logger.info("schema_fetch | ds=%s tables=%d", datasource.name, len(tables))
    return {
        "tables": tables,
        "columns": columns,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }


async def test_connection(datasource: Datasource) -> tuple[bool, str]:
    try:
        password = decrypt_secret(datasource.password_encrypted)
        conn = await aiomysql.connect(
            host=datasource.host,
            port=datasource.port,
            user=datasource.username,
            password=password,
            db=datasource.database_name,
            connect_timeout=5,
        )
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1")
        conn.close()
        return True, "OK"
    except Exception as e:
        logger.error("Datasource test failed [%s:%s/%s]: %s",
                     datasource.host, datasource.port, datasource.database_name, e)
        return False, str(e)
