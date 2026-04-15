"""
Central chat orchestrator. Receives messages, manages LLM calls,
dispatches tool use, streams WebSocket events, enforces permissions.
"""
import json
import uuid
import time
import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import WebSocket

from app.models.conversation import Conversation, Message
from app.models.ai_model import AIModel
from app.models.user import User
from app.services.llm_service import get_model_stream, StreamEvent
from app.config import settings

logger = logging.getLogger("app.chat")

# Max chars per tool result injected into LLM context (≈1 000 tokens).
# Full results are still streamed to the frontend — only the LLM context copy is compressed.
_MAX_TOOL_RESULT_CHARS = 4000


def _compress_tool_result(result: Any) -> Any:
    """Truncate oversized tool results before adding them to LLM message history."""
    serialized = json.dumps(result) if not isinstance(result, str) else result
    if len(serialized) <= _MAX_TOOL_RESULT_CHARS:
        return result
    if isinstance(result, list):
        kept = result[:20]
        kept.append({"_note": f"[Truncated: showing 20 of {len(result)} rows. Full result visible in UI.]"})
        logger.info("context_compress | %d rows → 20 rows (saved %d chars)", len(result), len(serialized) - len(json.dumps(kept)))
        return kept
    if isinstance(result, str):
        return result[:_MAX_TOOL_RESULT_CHARS] + f"\n[Truncated: {len(result) - _MAX_TOOL_RESULT_CHARS} chars omitted]"
    return serialized[:_MAX_TOOL_RESULT_CHARS] + "\n[Truncated]"


SYSTEM_PROMPT = """You are an intelligent IT operations assistant. You help users:
- Diagnose and fix software bugs
- Query databases to find data issues
- Analyze application logs
- Understand codebases
- Run structured troubleshooting workflows (Skills)

When you use tools, explain clearly what you're doing and why.
Present data results in a structured, readable format.
For bug diagnosis, provide a clear root cause analysis and actionable fix recommendations.

## Strict Scope Rules (MUST follow)

**Only use resources that are directly relevant to the user's question.**

1. **Match first, then act.** Before calling any tool, identify which specific resource (datasource, log source, code repo, document) the question is about. Only call tools against that resource.

2. **No scope expansion.** If the relevant resource is not configured or returns no useful results, do NOT broaden the search to unrelated resources. For example:
   - User asks about "finance system" → only search finance-related datasources/repos/docs. If none exist, stop.
   - A grep returns no matches → do NOT re-run the same search on a different unrelated repo.
   - A database query fails → do NOT query a different unrelated database.

3. **Admit the limit, stop early.** If after 1–2 targeted attempts the relevant resource is unavailable or has no data, respond honestly:
   - "The finance production database is not configured in my accessible datasources."
   - "No knowledge documents or code repositories for the finance system are available to me."
   Do NOT keep trying with other resources hoping to find something relevant.

4. **Maximum 3 tool calls per distinct sub-question.** If you haven't found the answer in 3 attempts on the relevant resource, conclude with what you know and state what is missing.

5. **Never search a resource just because it is available.** Availability does not imply relevance.

## Code Execution (run_python)
When a task requires complex computation, large-scale data aggregation, statistical analysis, report generation, or data visualization, use the `run_python` tool.

- **Use it when**: calculating quarterly/annual statistics, generating charts, exporting Excel/CSV reports, financial analysis, any computation that would be slow or token-heavy to perform inline.
- **Available packages**: pandas, numpy, matplotlib, seaborn, openpyxl, sqlalchemy, pymysql, scipy, requests
- **Database access**: use `os.environ['DS_{DATASOURCE_NAME}']` as a SQLAlchemy connection string (uppercase, spaces→underscores). Example: `engine = sqlalchemy.create_engine(os.environ['DS_MAIN_DB'])`
- **Output files**: save charts and files to `/output/` — they appear automatically in the Results panel.
  - Charts: `plt.savefig('/output/chart.png', dpi=150, bbox_inches='tight')`
  - Excel: `df.to_excel('/output/report.xlsx', index=False)`
- **Always** print a text summary to stdout so the answer is visible in the chat, even when files are also produced.
- Within a conversation, the Python kernel is **stateful** — variables and DataFrames from previous `run_python` calls are available in subsequent calls."""


async def send_ws(ws: WebSocket, event_type: str, **data):
    """Send a typed event over WebSocket."""
    await ws.send_json({"type": event_type, **data})


async def handle_chat_message(
    ws: WebSocket,
    conversation_id: uuid.UUID,
    user_message: str,
    model_id_override: str | None,
    current_user: User,
    db: AsyncSession,
    progress: dict | None = None,
):
    """
    Main chat loop:
    1. Save user message
    2. Load conversation history + model config
    3. Auto-inject all public skill system prompts
    4. Stream LLM response (with tool use loop)
    5. Save assistant message
    """
    # Verify conversation ownership
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        await send_ws(ws, "error", code="not_found", message="Conversation not found")
        return

    logger.info("chat | user=%s conv=%s msg=%r", current_user.username, str(conversation_id)[:8], user_message[:80])

    # Save user message
    user_msg = Message(
        conversation_id=conversation_id,
        role="user",
        content=user_message,
    )
    db.add(user_msg)
    await db.commit()
    await db.refresh(user_msg)

    # Load message history (last 50 messages for context)
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(50)
    )
    history = history_result.scalars().all()

    # Build messages array for LLM
    # Keep last 20 messages verbatim; compress older ones to 300 chars to save tokens
    _RECENT_KEEP = 20
    total_history = len(history)
    messages = []
    for i, msg in enumerate(history):
        if msg.role not in ("user", "assistant"):
            continue
        content = msg.content or ""
        if i < total_history - _RECENT_KEEP and len(content) > 300:
            content = content[:300] + f"... [truncated {len(content) - 300} chars]"
        messages.append({"role": msg.role, "content": content})

    # Load model config
    model_config = await _get_model_config(db, conv, model_id_override, current_user)

    # Auto-inject all public skill system prompts
    system_text = SYSTEM_PROMPT
    skills_prompt = await _get_all_public_skill_prompts(db)
    if skills_prompt:
        system_text = skills_prompt + "\n\n" + SYSTEM_PROMPT

    # Get available tools for this user
    tools = await _get_user_tools(db, current_user)

    # Prepend schema context so AI knows which datasources/tables to query
    schema_ctx = await _get_schema_context(db, current_user)
    if schema_ctx:
        system_text = system_text + "\n\n" + schema_ctx
        ds_count = schema_ctx.count("datasource_id:")
        logger.info("schema_ctx | %d datasource(s) injected into system prompt", ds_count)

    # Use Anthropic prompt caching to reduce token cost on repeated turns.
    # Only enabled for direct Anthropic API (no base_url override) — Bedrock and
    # other proxies do not support the prompt-caching-2024-07-31 beta flag.
    direct_anthropic = model_config["provider"] == "anthropic" and not model_config.get("base_url")
    if direct_anthropic:
        system: str | list = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
    else:
        system = system_text

    logger.info("llm_start | model=%s skill=%s tools=%d",
                model_config["model_id"],
                "public_skills" if skills_prompt else "none",
                len(tools))

    # LLM agentic loop (handles multi-step tool use)
    full_content = ""
    all_tool_calls = []
    all_artifacts = []
    total_tokens = 0

    max_iterations = 10  # prevent infinite loops
    t_start = time.monotonic()
    for iteration in range(max_iterations):
        if progress is not None:
            progress["status"] = "thinking"
            progress["iteration"] = iteration
        tool_results = []
        current_tool_calls: list[dict] = []
        text_buffer = ""

        stream = get_model_stream(
            provider=model_config["provider"],
            model_id=model_config["model_id"],
            api_key_encrypted=model_config.get("api_key_encrypted"),
            base_url=model_config.get("base_url"),
            messages=messages,
            system=system,
            tools=tools if tools else None,
            max_tokens=16000,
        )

        async for event in stream:
            if event.type == "text_delta":
                text_buffer += event.data["delta"]
                await send_ws(ws, "text_delta", delta=event.data["delta"])

            elif event.type == "tool_start":
                tool_use_id = event.data["tool_use_id"]
                tool_name = event.data["tool_name"]
                logger.info("tool_start | iter=%d tool=%s id=%s", iteration, tool_name, tool_use_id[:8])
                if progress is not None:
                    progress["status"] = f"tool:{tool_name}"
                current_tool_calls.append({
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "input": None,
                    "output": None,
                    "status": "running",
                })
                await send_ws(ws, "tool_start", tool=tool_name, tool_use_id=tool_use_id)

            elif event.type == "tool_input":
                tool_use_id = event.data["tool_use_id"]
                tool_input = event.data["tool_input"]
                # Find matching tool call
                for tc in current_tool_calls:
                    if tc["tool_use_id"] == tool_use_id:
                        tc["input"] = tool_input if isinstance(tool_input, dict) else json.loads(tool_input)
                        break

            elif event.type == "message_done":
                total_tokens += event.data.get("input_tokens", 0) + event.data.get("output_tokens", 0)
                stop_reason = event.data.get("stop_reason", "end_turn")

                if text_buffer:
                    full_content += text_buffer
                    logger.info("llm_text | iter=%d tokens_in=%d tokens_out=%d stop=%s text=%r",
                                iteration,
                                event.data.get("input_tokens", 0),
                                event.data.get("output_tokens", 0),
                                stop_reason,
                                text_buffer[:500])

                # Execute tool calls
                if stop_reason == "tool_use" and current_tool_calls:
                    # Add assistant message with tool use to history
                    messages.append({
                        "role": "assistant",
                        "content": _build_assistant_content(text_buffer, current_tool_calls, event.data.get("content", [])),
                    })

                    tool_result_content = []
                    for tc in current_tool_calls:
                        t_tool = time.monotonic()
                        # Log what we're about to execute
                        _log_tool_call(tc["tool_name"], tc["input"] or {}, iteration)
                        result, artifact = await _execute_tool(tc["tool_name"], tc["input"] or {}, current_user, db, str(conversation_id))
                        elapsed_ms = int((time.monotonic() - t_tool) * 1000)
                        # Log the result
                        _log_tool_result(tc["tool_name"], result, elapsed_ms)
                        tc["output"] = result
                        tc["status"] = "done"

                        if artifact:
                            all_artifacts.append(artifact)
                            await send_ws(ws, "tool_result",
                                         tool=tc["tool_name"],
                                         tool_use_id=tc["tool_use_id"],
                                         result=result,
                                         artifact=artifact)
                        else:
                            await send_ws(ws, "tool_result",
                                         tool=tc["tool_name"],
                                         tool_use_id=tc["tool_use_id"],
                                         result=result)

                        tool_result_content.append({
                            "type": "tool_result",
                            "tool_use_id": tc["tool_use_id"],
                            "content": json.dumps(_compress_tool_result(result)) if not isinstance(result, str) else result,
                        })
                        all_tool_calls.append(tc)

                    # Add tool results to message history
                    messages.append({"role": "user", "content": tool_result_content})
                    # Continue loop for next LLM call
                    break

                else:
                    # Finished (end_turn or no more tool use)
                    all_tool_calls.extend(current_tool_calls)
                    break

        else:
            # Exited for loop without break = done
            break

        if stop_reason := locals().get("stop_reason", "end_turn"):
            if stop_reason != "tool_use":
                break

    # Strip Bedrock XML tool artifacts before saving
    import re as _re
    full_content = _re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', full_content)
    full_content = _re.sub(r'<tool_response>[\s\S]*?</tool_response>', '', full_content)
    full_content = full_content.strip()

    logger.info("llm_reply | conv=%s content=%r", str(conversation_id)[:8], full_content[:600])

    # Save assistant message
    assistant_msg = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=full_content,
        tool_calls=all_tool_calls if all_tool_calls else None,
        artifacts=all_artifacts if all_artifacts else None,
        token_count=total_tokens,
    )
    db.add(assistant_msg)

    # Update conversation timestamp + auto-title if needed
    if not conv.title or conv.title == "New Chat":
        conv.title = _auto_title(user_message)
    await db.commit()
    await db.refresh(assistant_msg)

    total_ms = int((time.monotonic() - t_start) * 1000)
    logger.info("chat_done | conv=%s tokens=%d tools=%d elapsed=%dms title=%r",
                str(conversation_id)[:8], total_tokens, len(all_tool_calls), total_ms, conv.title)

    await send_ws(ws, "message_done",
                  message_id=str(assistant_msg.id),
                  token_count=total_tokens,
                  title=conv.title)


def _build_assistant_content(text: str, tool_calls: list[dict], raw_content: list) -> list:
    """Build the assistant content block for Anthropic's format."""
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for block in raw_content:
        if hasattr(block, "type") and block.type == "tool_use":
            content.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return content


async def _get_model_config(db: AsyncSession, conv: Conversation, model_id_override: str | None, user: User) -> dict:
    """Get the model configuration to use for this conversation."""
    model_id = model_id_override or conv.model_id
    if model_id:
        result = await db.execute(select(AIModel).where(AIModel.id == model_id, AIModel.is_active == True))
        model = result.scalar_one_or_none()
        if model:
            return {
                "provider": model.provider,
                "model_id": model.model_id,
                "api_key_encrypted": model.api_key_encrypted,
                "base_url": model.base_url,
            }

    # Fall back to default model in DB
    result = await db.execute(
        select(AIModel).where(AIModel.is_active == True, AIModel.is_default == True).limit(1)
    )
    model = result.scalar_one_or_none()
    if not model:
        # No default set — use any active model
        result = await db.execute(select(AIModel).where(AIModel.is_active == True).limit(1))
        model = result.scalar_one_or_none()
    if model:
        return {
            "provider": model.provider,
            "model_id": model.model_id,
            "api_key_encrypted": model.api_key_encrypted,
            "base_url": model.base_url,
        }

    # Last resort: env vars (will fail loudly if ANTHROPIC_API_KEY not set)
    return {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-6",
        "api_key_encrypted": None,
        "base_url": None,
    }


async def _get_all_public_skill_prompts(db: AsyncSession) -> str | None:
    """Auto-load all public system_prompt skills and merge them into context."""
    from app.models.skill_mcp import Skill
    result = await db.execute(
        select(Skill).where(Skill.is_public == True, Skill.type == "system_prompt")
    )
    skills = result.scalars().all()
    parts = [s.system_prompt for s in skills if s.system_prompt]
    return "\n\n".join(parts) if parts else None


async def _get_skill_system_prompt(db: AsyncSession, skill_id: str, user: User) -> str | None:
    from app.models.skill_mcp import Skill, UserSkillPermission
    from sqlalchemy import or_

    result = await db.execute(
        select(Skill).where(
            Skill.id == skill_id,
            Skill.type == "system_prompt",
            or_(
                Skill.is_public == True,
                Skill.created_by == user.id,
            )
        )
    )
    skill = result.scalar_one_or_none()
    return skill.system_prompt if skill else None


async def _get_user_tools(db: AsyncSession, user: User) -> list[dict]:
    """Build tool list directly from the user's accessible resources (no separate MCP permission gate)."""
    from app.models.datasource import Datasource, UserDatasourcePermission
    from app.models.knowledge import LogSource, UserLogPermission, KnowledgeSource, UserKnowledgePermission

    tools = []

    # ── MySQL: tools if user has any accessible datasource ─────────────────
    if user.role == "admin":
        r = await db.execute(select(Datasource).where(Datasource.is_active == True))
        datasources = r.scalars().all()
    else:
        r = await db.execute(
            select(Datasource)
            .join(UserDatasourcePermission, Datasource.id == UserDatasourcePermission.datasource_id)
            .where(UserDatasourcePermission.user_id == user.id, Datasource.is_active == True)
        )
        datasources = r.scalars().all()
    if datasources:
        tools.extend(_mysql_tools(datasources))

    # ── Log search: tools if user has any accessible log source ────────────
    if user.role == "admin":
        r = await db.execute(select(LogSource).where(LogSource.is_active == True))
        log_sources = r.scalars().all()
    else:
        r = await db.execute(
            select(LogSource)
            .join(UserLogPermission, LogSource.id == UserLogPermission.log_id)
            .where(UserLogPermission.user_id == user.id, LogSource.is_active == True)
        )
        log_sources = r.scalars().all()
    if log_sources:
        tools.extend(_log_tools(log_sources))

    # ── Knowledge + Code: tools if user has any accessible knowledge source ─
    if user.role == "admin":
        r = await db.execute(select(KnowledgeSource))
        knowledge_sources = r.scalars().all()
    else:
        r = await db.execute(
            select(KnowledgeSource)
            .join(UserKnowledgePermission, KnowledgeSource.id == UserKnowledgePermission.knowledge_id)
            .where(UserKnowledgePermission.user_id == user.id)
        )
        knowledge_sources = r.scalars().all()

    has_docs = any(k.type == "document" and k.status == "ready" for k in knowledge_sources)
    code_sources = [k for k in knowledge_sources if k.type == "github_repo" and k.status == "ready"]

    if has_docs:
        tools.extend(await _knowledge_tools(db, user))
    if code_sources:
        tools.extend(_code_tools(code_sources))

    # ── Python code execution: available when user has any datasource ──────
    from app.services.code_execution_service import container_pool
    if datasources and container_pool.available:
        ds_names = ", ".join(f"DS_{ds.name.replace(' ', '_').replace('-', '_').upper()}" for ds in datasources)
        tools.append({
            "name": "run_python",
            "description": f"""Execute a Python script in an isolated sandbox to perform complex computation, data analysis, or report generation.

Available database env vars (SQLAlchemy connection strings): {ds_names}
  Example: engine = sqlalchemy.create_engine(os.environ['DS_MAIN_DB'])

Available packages: pandas, numpy, matplotlib, seaborn, openpyxl, sqlalchemy, pymysql, scipy, requests
Output directory: /output/ — save charts (PNG) and files (XLSX, CSV) here; they appear in the Results panel.
  - plt.savefig('/output/chart.png', dpi=150, bbox_inches='tight')
  - df.to_excel('/output/report.xlsx', index=False)

Always print a text summary to stdout.
The kernel is stateful within this conversation — prior variables remain available.""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Complete Python script to execute",
                    },
                    "datasource_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "UUIDs of datasources this code needs (env vars will be injected)",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line description of what this code does",
                    },
                },
                "required": ["code", "description"],
            },
        })

    return tools


async def _get_schema_context(db: AsyncSession, user: User) -> str:
    """Build a schema context block from cached schema stored on each datasource.
    Falls back to a short note if cache is missing (no live MySQL query)."""
    from app.models.datasource import Datasource, UserDatasourcePermission

    if user.role == "admin":
        r = await db.execute(select(Datasource).where(Datasource.is_active == True))
        datasources = r.scalars().all()
    else:
        r = await db.execute(
            select(Datasource)
            .join(UserDatasourcePermission, Datasource.id == UserDatasourcePermission.datasource_id)
            .where(UserDatasourcePermission.user_id == user.id, Datasource.is_active == True)
        )
        datasources = r.scalars().all()

    if not datasources:
        return ""

    lines = ["## Available Databases"]
    for ds in datasources:
        lines.append(f"\n### {ds.name}")
        lines.append(f"datasource_id: {ds.id} | database: {ds.database_name} | host: {ds.host}:{ds.port}")

        cache = ds.schema_cache
        if cache and cache.get("tables"):
            columns = cache.get("columns", {})
            table_lines = []
            for table in cache["tables"]:
                cols = columns.get(table, [])
                if cols:
                    col_str = ", ".join(f'{c["name"]} ({c["type"]})' for c in cols)
                    table_lines.append(f"  - {table}: {col_str}")
                else:
                    table_lines.append(f"  - {table}")
            lines.append("Tables:")
            lines.extend(table_lines)
        else:
            lines.append("Tables: (schema not cached — use list_tables tool to fetch)")

    return "\n".join(lines)


def _log_tool_call(tool_name: str, tool_input: dict, iteration: int) -> None:
    """Log a tool call with its key parameters in a human-readable format."""
    try:
        if tool_name == "query_database":
            logger.info("tool_call | iter=%d tool=query_database ds=%s sql=%s",
                        iteration, tool_input.get("datasource_id", "?")[:8],
                        tool_input.get("sql", "")[:300])
        elif tool_name == "list_tables":
            logger.info("tool_call | iter=%d tool=list_tables ds=%s",
                        iteration, tool_input.get("datasource_id", "?")[:8])
        elif tool_name == "search_logs":
            logger.info("tool_call | iter=%d tool=search_logs source=%s query=%r level=%s range=%s limit=%s",
                        iteration,
                        str(tool_input.get("source_id", "?"))[:8],
                        tool_input.get("query", "")[:200],
                        tool_input.get("level", "-"),
                        tool_input.get("time_range", "1h"),
                        tool_input.get("limit", 20))
        elif tool_name == "grep_code":
            logger.info("tool_call | iter=%d tool=grep_code source=%s pattern=%r glob=%s",
                        iteration,
                        str(tool_input.get("source_id", "?"))[:8],
                        tool_input.get("pattern", ""),
                        tool_input.get("file_glob", "*"))
        elif tool_name == "read_code_file":
            logger.info("tool_call | iter=%d tool=read_code_file source=%s file=%s lines=%s-%s",
                        iteration,
                        str(tool_input.get("source_id", "?"))[:8],
                        tool_input.get("file_path", ""),
                        tool_input.get("start_line", 1),
                        tool_input.get("end_line", "end"))
        elif tool_name == "read_knowledge_document":
            logger.info("tool_call | iter=%d tool=read_knowledge_document name=%r",
                        iteration, tool_input.get("name", ""))
        elif tool_name == "run_python":
            logger.info("tool_call | iter=%d tool=run_python desc=%r datasources=%d code_lines=%d",
                        iteration,
                        tool_input.get("description", "")[:100],
                        len(tool_input.get("datasource_ids", [])),
                        len(tool_input.get("code", "").splitlines()))
        else:
            logger.info("tool_call | iter=%d tool=%s input=%s",
                        iteration, tool_name,
                        json.dumps(tool_input, ensure_ascii=False)[:200])
    except Exception:
        logger.info("tool_call | iter=%d tool=%s input=<unserializable>", iteration, tool_name)


def _log_tool_result(tool_name: str, result: Any, elapsed_ms: int) -> None:
    """Log the result of a tool execution with a data preview."""
    is_error = isinstance(result, dict) and "error" in result
    if is_error:
        logger.error("tool_result | tool=%s elapsed=%dms ERROR=%s msg=%s",
                     tool_name, elapsed_ms,
                     result.get("error"), result.get("message", ""))
        return

    if isinstance(result, list):
        rows = len(result)
        preview = ""
        if rows > 0:
            try:
                preview = json.dumps(result[:3], ensure_ascii=False)[:400]
            except Exception:
                preview = str(result[:3])[:400]
        logger.info("tool_result | tool=%s elapsed=%dms rows=%d preview=%s",
                    tool_name, elapsed_ms, rows, preview)
    elif isinstance(result, str):
        logger.info("tool_result | tool=%s elapsed=%dms len=%d content=%r",
                    tool_name, elapsed_ms, len(result), result[:400])
    else:
        logger.info("tool_result | tool=%s elapsed=%dms result=%s",
                    tool_name, elapsed_ms, str(result)[:300])


async def _execute_tool(tool_name: str, tool_input: dict, user: User, db: AsyncSession, conversation_id: str = "") -> tuple[Any, dict | None]:
    """Route tool calls to appropriate service. Returns (result, artifact_or_None)."""
    try:
        if tool_name == "query_database":
            from app.services.mysql_service import execute_query
            result = await execute_query(
                datasource_id=tool_input.get("datasource_id"),
                sql=tool_input.get("sql", ""),
                user=user,
                db=db,
            )
            artifact = {"type": "table", "data": result} if isinstance(result, list) else None
            return result, artifact

        elif tool_name == "list_tables":
            from app.services.mysql_service import list_tables
            result = await list_tables(tool_input.get("datasource_id"), user, db)
            return result, {"type": "code", "language": "text", "content": "\n".join(result)}

        elif tool_name == "search_logs":
            from app.services.log_service import search_logs
            result = await search_logs(
                source_id=tool_input.get("source_id"),
                query=tool_input.get("query", ""),
                level=tool_input.get("level"),
                time_range=tool_input.get("time_range", "1h"),
                limit=int(tool_input.get("limit", 20)),
                user=user,
                db=db,
            )
            artifact = {"type": "log", "content": result}
            return result, artifact

        elif tool_name == "read_knowledge_document":
            from app.services.knowledge_service import read_knowledge_document
            result = await read_knowledge_document(
                name=tool_input.get("name", ""),
                user=user,
                db=db,
            )
            return result, {"type": "text", "content": result}

        elif tool_name in ("grep_code", "read_code_file", "list_code_files", "git_log"):
            from app.services.knowledge_service import grep_code, read_code_file, list_code_files, git_log
            source_id = tool_input.get("source_id")
            if not source_id:
                return "Missing source_id", None
            from app.models.knowledge import KnowledgeSource
            src = await db.get(KnowledgeSource, uuid.UUID(str(source_id)))
            if not src or not src.clone_path:
                return f"Repository {source_id} not cloned or not found", None

            if tool_name == "grep_code":
                result = await grep_code(
                    clone_path=src.clone_path,
                    pattern=tool_input.get("pattern", ""),
                    file_glob=tool_input.get("file_glob"),
                    case_insensitive=tool_input.get("case_insensitive", False),
                    max_results=tool_input.get("max_results", 50),
                )
            elif tool_name == "read_code_file":
                result = await read_code_file(
                    clone_path=src.clone_path,
                    file_path=tool_input.get("file_path", ""),
                    start_line=tool_input.get("start_line", 1),
                    end_line=tool_input.get("end_line"),
                )
            elif tool_name == "list_code_files":
                result = await list_code_files(
                    clone_path=src.clone_path,
                    path=tool_input.get("path", ""),
                    extension=tool_input.get("extension"),
                )
            else:  # git_log
                result = await git_log(
                    clone_path=src.clone_path,
                    max_commits=tool_input.get("max_commits", 20),
                    file_path=tool_input.get("file_path"),
                )
            lang = _detect_lang(tool_input.get("file_path", ""))
            return result, {"type": "code", "language": lang, "content": result}

        elif tool_name == "run_python":
            from app.services.code_execution_service import execute_python
            exec_result = await execute_python(
                code=tool_input.get("code", ""),
                conversation_id=conversation_id,
                datasource_ids=tool_input.get("datasource_ids") or [],
                user=user,
                db=db,
            )
            # Build structured result for LLM context
            result = {
                "exit_code": exec_result.exit_code,
                "stdout": exec_result.stdout,
                "stderr": exec_result.stderr[:500] if exec_result.stderr else "",
                "elapsed_ms": exec_result.elapsed_ms,
                "artifacts_count": len(exec_result.artifacts),
            }
            # Build artifacts for frontend — images and files
            fe_artifacts = []
            for art in exec_result.artifacts:
                if art.mime_type.startswith("image/"):
                    fe_artifacts.append({
                        "type": "image",
                        "name": art.name,
                        "data": art.data_b64,
                        "mime": art.mime_type,
                    })
                else:
                    fe_artifacts.append({
                        "type": "file",
                        "name": art.name,
                        "data": art.data_b64,
                        "mime": art.mime_type,
                    })

            if len(fe_artifacts) == 1:
                artifact = fe_artifacts[0]
            elif len(fe_artifacts) > 1:
                artifact = {"type": "execution", "artifacts": fe_artifacts}
            else:
                # stdout-only result
                artifact = {"type": "text", "content": exec_result.stdout} if exec_result.stdout else None
            return result, artifact

        else:
            return f"Unknown tool: {tool_name}", None

    except PermissionError as e:
        return {"error": "permission_denied", "message": str(e)}, None
    except Exception as e:
        return {"error": str(e)}, None


def _detect_lang(path: str) -> str:
    ext_map = {".py": "python", ".ts": "typescript", ".js": "javascript", ".sql": "sql", ".go": "go", ".java": "java"}
    for ext, lang in ext_map.items():
        if path.endswith(ext):
            return lang
    return "text"


def _auto_title(message: str) -> str:
    return message[:60] + ("..." if len(message) > 60 else "")


def _mysql_tools(datasources=None) -> list[dict]:
    ds_lines = ""
    if datasources:
        entries = [f"  - {ds.name} (datasource_id: {ds.id}, database: {ds.database_name})" for ds in datasources]
        ds_lines = "\n\nAvailable datasources:\n" + "\n".join(entries)
    return [
        {
            "name": "query_database",
            "description": (
                "Execute a SQL SELECT query on a configured MySQL datasource. "
                "Use this to look up data, diagnose data issues, or verify records. "
                "Always add LIMIT (max 100) unless you need a full count."
                + ds_lines
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "datasource_id": {"type": "string", "description": "The UUID of the datasource to query (see available datasources above)"},
                    "sql": {"type": "string", "description": "The SQL SELECT statement to execute"},
                },
                "required": ["datasource_id", "sql"],
            },
        },
        {
            "name": "list_tables",
            "description": "List all available tables in a MySQL datasource" + ds_lines,
            "input_schema": {
                "type": "object",
                "properties": {
                    "datasource_id": {"type": "string", "description": "The UUID of the datasource"},
                },
                "required": ["datasource_id"],
            },
        },
    ]


def _log_tools(log_sources=None) -> list[dict]:
    src_lines = ""
    if log_sources:
        entries = []
        for s in log_sources:
            line = f"  - {s.name} (source_id: {s.id}, type: {s.type})"
            if s.description:
                line += f"\n    Label hints: {s.description}"
            entries.append(line)
        src_lines = "\n\nAvailable log sources:\n" + "\n".join(entries)
    return [
        {
            "name": "search_logs",
            "description": (
                "Search application logs for errors, warnings, or specific patterns. "
                "Supports file logs, Elasticsearch, and Grafana Loki sources.\n\n"
                "**Query strategy — minimize token consumption:**\n"
                "1. Always start with a SPECIFIC query (keyword, trace ID, error message) and a small limit (10–20).\n"
                "2. If the first query returns too little, refine the query or widen the time range — do NOT increase limit blindly.\n"
                "3. Only increase limit when you already know the pattern and need more samples.\n"
                "4. Never query without a line filter (e.g. `{server=\"x\"}` alone) — always add `|= \\`keyword\\``.\n\n"
                "For Loki: `query` must be a full LogQL expression with stream selector from the label hints, "
                'e.g. `{server="myapp"} |= \\`error\\``. '
                "Do NOT use `{job=~\".+\"}` — it is too broad.\n\n"
                "When user asks for 'last N logs', set `limit=N` and use a broad but not empty line filter "
                "(e.g. the service name as keyword) to avoid scanning all streams."
                + src_lines
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "Log source UUID (see available sources above)"},
                    "query": {"type": "string", "description": "LogQL expression with stream selector + line filter, e.g. `{server=\"myapp\"} |= \\`keyword\\``"},
                    "level": {"type": "string", "enum": ["ERROR", "WARN", "INFO", "DEBUG"], "description": "Filter by log level"},
                    "time_range": {"type": "string", "description": "Time range: '15m', '1h', '24h', '7d'. Start small, expand if needed."},
                    "limit": {"type": "integer", "description": "Max entries to return. Default is 20. Start small (10–20) for exploration, up to 100 for broader analysis. Hard max: 500."},
                },
                "required": ["source_id", "query"],
            },
        }
    ]


async def _knowledge_tools(db: AsyncSession, user: User) -> list[dict]:
    from app.services.knowledge_service import list_knowledge_documents
    docs = await list_knowledge_documents(user, db)
    doc_list = "\n".join(
        f'  - "{d["name"]}"' + (f': {d["description"]}' if d["description"] else "")
        for d in docs
    ) if docs else "  (none indexed yet)"

    return [
        {
            "name": "read_knowledge_document",
            "description": (
                "Read a complete knowledge document by name. Use this when you need full reference material "
                "for a specific domain (e.g. database schema, reconciliation flow, API docs). "
                "Returns the entire document — no information loss from chunking.\n\n"
                f"Available documents:\n{doc_list}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Document name to retrieve (e.g. 'schema', 'reconciliation', 'order_query')",
                    },
                },
                "required": ["name"],
            },
        },
    ]


def _code_tools(code_sources=None) -> list[dict]:
    if not code_sources:
        return []
    repo_list = "\n".join(
        f"  - \"{s.name}\" (source_id: {s.id}, url: {s.repo_url})"
        for s in code_sources
    )
    return [
        {
            "name": "grep_code",
            "description": (
                "Search for a pattern across all files in a cloned code repository using grep. "
                "Returns file:line:match results. Use this to find function definitions, error messages, "
                "variable names, or any text pattern in the codebase.\n\n"
                f"Available repositories:\n{repo_list}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "Knowledge source ID of the repo"},
                    "pattern": {"type": "string", "description": "Search pattern (supports basic regex)"},
                    "file_glob": {"type": "string", "description": "Filter files by glob, e.g. '*.py', '*.ts'"},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive search"},
                    "max_results": {"type": "integer", "description": "Max lines to return (default 50)"},
                },
                "required": ["source_id", "pattern"],
            },
        },
        {
            "name": "read_code_file",
            "description": (
                "Read the content of a specific file from a cloned repository. "
                "Optionally specify a line range to read a portion of a large file.\n\n"
                f"Available repositories:\n{repo_list}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "Knowledge source ID of the repo"},
                    "file_path": {"type": "string", "description": "Relative file path within the repo, e.g. 'src/api/handler.py'"},
                    "start_line": {"type": "integer", "description": "First line to read (1-based, default 1)"},
                    "end_line": {"type": "integer", "description": "Last line to read (default: end of file)"},
                },
                "required": ["source_id", "file_path"],
            },
        },
        {
            "name": "list_code_files",
            "description": (
                "List all code files in a cloned repository, with optional directory and extension filters.\n\n"
                f"Available repositories:\n{repo_list}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "Knowledge source ID of the repo"},
                    "path": {"type": "string", "description": "Subdirectory to list (empty = repo root)"},
                    "extension": {"type": "string", "description": "Filter by extension, e.g. 'py', 'ts', 'go'"},
                },
                "required": ["source_id"],
            },
        },
        {
            "name": "git_log",
            "description": (
                "Get recent git commit history for a cloned repository. "
                "Optionally filter to commits that touched a specific file.\n\n"
                f"Available repositories:\n{repo_list}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "Knowledge source ID of the repo"},
                    "max_commits": {"type": "integer", "description": "Number of commits to return (default 20)"},
                    "file_path": {"type": "string", "description": "Only show commits that modified this file"},
                },
                "required": ["source_id"],
            },
        },
    ]
