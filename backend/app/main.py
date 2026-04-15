from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import logging.config

from app.config import settings
from app.database import engine, Base
from app.api.router import api_router


def _configure_logging():
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "app": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "app",
            }
        },
        "root": {"level": "INFO", "handlers": ["console"]},
    })
    # Explicitly silence noisy libraries after dictConfig
    for name in [
        "sqlalchemy.engine", "sqlalchemy.engine.Engine",
        "sqlalchemy.pool", "sqlalchemy.orm",
        "aiomysql", "httpx", "httpcore",
        "anthropic", "openai",
    ]:
        logging.getLogger(name).setLevel(logging.WARNING)


async def _run_schema_migrations(conn):
    """Add columns that were introduced after the initial create_all."""
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE log_sources ADD COLUMN IF NOT EXISTS loki_url VARCHAR(500)",
        "ALTER TABLE log_sources ADD COLUMN IF NOT EXISTS loki_credentials_encrypted TEXT",
        "ALTER TABLE log_sources ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS clone_path VARCHAR(500)",
        "ALTER TABLE knowledge_sources ADD COLUMN IF NOT EXISTS access_token_encrypted TEXT",
        "ALTER TABLE datasources ADD COLUMN IF NOT EXISTS schema_cache JSONB",
        "ALTER TABLE datasources ADD COLUMN IF NOT EXISTS schema_cached_at TIMESTAMPTZ",
    ]
    for sql in migrations:
        try:
            await conn.execute(text(sql))
        except Exception:
            pass


async def _seed_defaults():
    """Seed builtin MCP servers and default Skills on first run (requires an admin user)."""
    from app.database import AsyncSessionLocal
    from sqlalchemy import select, func
    from app.models.user import User
    from app.models.skill_mcp import Skill, MCPServer

    async with AsyncSessionLocal() as db:
        admin_result = await db.execute(select(User).where(User.role == "admin").limit(1))
        admin = admin_result.scalar_one_or_none()
        if not admin:
            return  # No admin yet — seeding will happen after first registration triggers next startup

        # Builtin MCP servers
        count = (await db.execute(select(func.count()).select_from(MCPServer))).scalar()
        if count == 0:
            for key, name in [
                ("mysql", "MySQL Tool"),
                ("log", "Log Search"),
                ("code", "Code Explorer"),
                ("knowledge", "Knowledge Base"),
            ]:
                db.add(MCPServer(name=name, type="builtin", builtin_key=key, created_by=admin.id))

        # Default Skills
        skill_count = (await db.execute(select(func.count()).select_from(Skill))).scalar()
        if skill_count == 0:
            db.add(Skill(
                name="数据库分析 / Database Analysis",
                description="Guide AI to systematically query databases, diagnose data issues, and explain findings",
                category="Database",
                type="system_prompt",
                is_public=True,
                system_prompt=(
                    "You are a database analyst. When investigating data issues:\n"
                    "1. Call list_tables first to understand the schema, then use DESCRIBE or schema queries\n"
                    "2. Always add LIMIT (100 max) unless counting rows\n"
                    "3. Format results as clear tables; explain what each query checks\n"
                    "4. If you find anomalies, cross-check with related tables\n"
                    "5. End with a summary of findings and actionable recommendations\n\n"
                    "SQL best practices:\n"
                    "- Use table aliases, add WHERE clauses (time ranges, IDs, status)\n"
                    "- For aggregations always include GROUP BY\n"
                    "- Prefer COUNT(*) for row counting"
                ),
                created_by=admin.id,
            ))
            db.add(Skill(
                name="线上故障排查 / Incident Response",
                description="Diagnose production incidents by querying logs, code, and database systematically",
                category="Operations",
                type="system_prompt",
                is_public=True,
                system_prompt=(
                    "You are an expert SRE. When investigating a production incident:\n"
                    "1. Search logs for the error pattern in the relevant time window\n"
                    "2. Identify the root service and exact error message\n"
                    "3. Cross-reference with code (grep_code, read_code_file) to understand the logic\n"
                    "4. Query the database to check affected records and data consistency\n"
                    "5. Trace the full call chain: frontend → backend → database → external services\n\n"
                    "Produce a structured incident report:\n"
                    "- **Symptoms**: What failed, when, and impact scope\n"
                    "- **Root Cause**: Specific code/data/config that caused the failure\n"
                    "- **Evidence**: Log excerpts, query results, code references\n"
                    "- **Fix**: Specific code changes or data corrections needed\n"
                    "- **Prevention**: How to prevent recurrence\n\n"
                    "Log search tips: cover the incident window + 30 min before; "
                    "look for cascading errors across multiple services."
                ),
                created_by=admin.id,
            ))

        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    logger = logging.getLogger("app.startup")
    import asyncio
    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await _run_schema_migrations(conn)
            logger.info("Database ready")
            break
        except Exception as e:
            if attempt == 9:
                raise
            logger.warning("DB not ready (attempt %d/10): %s. Retrying in 3s...", attempt + 1, e)
            await asyncio.sleep(3)

    await _seed_defaults()
    logger.info("Startup complete — %s", settings.app_name)

    # Start sandbox container pool (non-fatal if Docker unavailable)
    from app.services.code_execution_service import container_pool
    await container_pool.start()

    yield

    await container_pool.shutdown()
    await engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name}
