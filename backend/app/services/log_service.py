"""Log query service supporting file logs, Elasticsearch, and Grafana Loki."""
import glob
import re
import os
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.knowledge import LogSource, UserLogPermission
from app.models.user import User
from app.utils.security import decrypt_secret

logger = logging.getLogger(__name__)


async def _get_log_source_with_permission(
    source_id: str,
    user: User,
    db: AsyncSession,
) -> LogSource:
    result = await db.execute(
        select(LogSource).where(LogSource.id == source_id, LogSource.is_active == True)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise ValueError(f"Log source {source_id} not found")

    if user.role == "admin":
        return source

    perm_result = await db.execute(
        select(UserLogPermission).where(
            UserLogPermission.user_id == user.id,
            UserLogPermission.log_id == source_id,
        )
    )
    if not perm_result.scalar_one_or_none():
        raise PermissionError(f"No permission to access log source {source_id}")
    return source


def _parse_time_range(time_range: str) -> datetime:
    """Parse '1h', '24h', '7d' into a datetime cutoff (UTC)."""
    now = datetime.now(tz=timezone.utc)
    match = re.match(r"(\d+)([hd])", time_range.lower())
    if not match:
        return now - timedelta(hours=1)
    value, unit = int(match.group(1)), match.group(2)
    if unit == "h":
        return now - timedelta(hours=value)
    return now - timedelta(days=value)


async def search_file_logs(
    source: LogSource,
    query: str,
    level: str | None,
    time_range: str,
    max_lines: int = 200,
) -> str:
    """Search file-based logs using glob pattern matching."""
    pattern = source.file_pattern or ""
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)[:10]

    if not files:
        return f"No log files found matching pattern: {pattern}"

    results = []
    query_lower = query.lower()
    level_filter = level.upper() if level else None

    for filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if level_filter and level_filter not in line.upper():
                        continue
                    if query_lower and query_lower not in line.lower():
                        continue
                    results.append(line)
                    if len(results) >= max_lines:
                        break
        except (IOError, OSError):
            continue
        if len(results) >= max_lines:
            break

    if not results:
        return f"No log entries found matching '{query}'" + (f" with level {level}" if level else "")

    return "\n".join(results)


async def search_elasticsearch_logs(
    source: LogSource,
    query: str,
    level: str | None,
    time_range: str,
    max_results: int = 100,
) -> str:
    """Search Elasticsearch logs."""
    try:
        from elasticsearch import AsyncElasticsearch

        creds_json = None
        if source.es_credentials_encrypted:
            import json
            creds_json = json.loads(decrypt_secret(source.es_credentials_encrypted))

        es_kwargs = {"hosts": [f"http://{source.es_host}:{source.es_port}"]}
        if creds_json:
            es_kwargs["basic_auth"] = (creds_json.get("username", ""), creds_json.get("password", ""))

        es = AsyncElasticsearch(**es_kwargs)

        cutoff = _parse_time_range(time_range)
        must_clauses = [
            {"query_string": {"query": query}},
            {"range": {"@timestamp": {"gte": cutoff.isoformat()}}},
        ]
        if level:
            must_clauses.append({"term": {"level": level.lower()}})

        response = await es.search(
            index=source.es_index_pattern or "*",
            body={
                "query": {"bool": {"must": must_clauses}},
                "sort": [{"@timestamp": {"order": "desc"}}],
                "size": max_results,
            }
        )
        await es.close()

        hits = response["hits"]["hits"]
        if not hits:
            return f"No logs found matching '{query}'"

        lines = []
        for hit in hits:
            src = hit["_source"]
            ts = src.get("@timestamp", "")
            lvl = src.get("level", "INFO").upper()
            msg = src.get("message", str(src))
            lines.append(f"[{ts}] [{lvl}] {msg}")
        return "\n".join(lines)

    except ImportError:
        return "Elasticsearch client not available"
    except Exception as e:
        return f"Elasticsearch error: {str(e)}"


async def search_loki_logs(
    source: LogSource,
    query: str,
    level: str | None,
    time_range: str,
    max_lines: int = 200,
) -> str:
    """Search Grafana Loki logs via the HTTP query_range API.

    query can be:
      - A complete LogQL expression: {server="myapp"} |= `error`
      - A plain text search string (auto-wrapped): error message text
    """
    try:
        import httpx
        import base64

        cutoff = _parse_time_range(time_range)
        now = datetime.now(tz=timezone.utc)

        # Detect if query is already a valid LogQL expression (starts with {)
        query = query.strip()
        if query.startswith("{"):
            # Use as-is — caller provided complete LogQL
            logql = query
            if level and "|" not in logql:
                logql += f' | json | level=~"(?i){level}"'
        else:
            # Plain text — wrap with source's description as stream selector (if set),
            # otherwise fall back to a broad selector.
            # description stores label hints like: server="myapp", env="prod"
            desc = (source.description or "").strip()
            if desc and not desc.startswith("{"):
                stream_selector = "{" + desc + "}"
            elif desc.startswith("{"):
                stream_selector = desc.split("}")[0] + "}"
            else:
                stream_selector = '{job=~".+"}'
            logql = stream_selector + " |= `" + query + "`"
            if level:
                logql += f' | json | level=~"(?i){level}"'

        # Use RFC3339 timestamps — nanosecond integers can cause parsing issues on some Loki deployments
        start_ts = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        headers: dict = {}
        if source.loki_credentials_encrypted:
            creds = __import__("json").loads(decrypt_secret(source.loki_credentials_encrypted))
            if "token" in creds:
                headers["Authorization"] = f"Bearer {creds['token']}"
            elif "username" in creds:
                raw = f"{creds['username']}:{creds.get('password', '')}"
                headers["Authorization"] = "Basic " + base64.b64encode(raw.encode()).decode()

        loki_base = source.loki_url.rstrip("/")
        query_url = f"{loki_base}/loki/api/v1/query_range"

        logger.info("Loki query | url=%s logql=%r range=%s", query_url, logql, time_range)

        async with httpx.AsyncClient(timeout=30.0, verify=True) as client:
            resp = await client.get(
                query_url,
                headers=headers,
                params={
                    "query": logql,
                    "start": start_ts,
                    "end": end_ts,
                    "limit": max_lines,
                    "step": "15s",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        streams = data.get("data", {}).get("result", [])
        if not streams:
            return f"No Loki logs found matching '{query}' in the last {time_range}"

        lines = []
        for stream in streams:
            labels = stream.get("stream", {})
            label_str = " ".join(
                f'{k}="{v}"' for k, v in labels.items()
                if k in ("app", "service", "container", "job", "server", "filename", "namespace")
            )
            for ts_ns, line in stream.get("values", []):
                ts = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                prefix = f"[{ts}]"
                if label_str:
                    prefix += f" [{label_str}]"
                lines.append(f"{prefix} {line}")
                if len(lines) >= max_lines:
                    break
            if len(lines) >= max_lines:
                break

        # streams come back newest-first; reverse for chronological order
        lines.reverse()
        return "\n".join(lines)

    except Exception as e:
        logger.error("Loki search failed [%s]: %s", getattr(source, "loki_url", "?"), e)
        return f"Loki error: {e}"


async def search_logs(
    source_id: str,
    query: str,
    level: str | None,
    time_range: str,
    user: User,
    db: AsyncSession,
    limit: int = 100,
) -> str:
    source = await _get_log_source_with_permission(source_id, user, db)

    if source.type == "file":
        return await search_file_logs(source, query, level, time_range, max_lines=limit)
    elif source.type == "elasticsearch":
        return await search_elasticsearch_logs(source, query, level, time_range, max_results=limit)
    elif source.type == "loki":
        return await search_loki_logs(source, query, level, time_range, max_lines=limit)
    else:
        return f"Unsupported log source type: {source.type}"
