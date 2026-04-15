"""
Knowledge base service: document full-file access and git repository tools.

Documents are read directly from disk — no chunking or vector indexing.
Code repositories are cloned locally via git; AI uses grep/read/list/log tools.
"""
import os
import asyncio
import subprocess
from pathlib import Path
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.knowledge import KnowledgeSource, UserKnowledgePermission
from app.models.user import User
from app.config import settings


# ─── Git Repo Cloning ────────────────────────────────────────────────────────

async def clone_repo(source: KnowledgeSource) -> str:
    """Clone a git repository to local disk. Returns clone_path. Raises on failure."""
    from app.utils.security import decrypt_secret
    from urllib.parse import urlparse

    source_id = str(source.id)
    clone_dir = os.path.join(settings.repos_dir, source_id)

    # Remove stale clone if exists
    if os.path.exists(clone_dir):
        import shutil
        shutil.rmtree(clone_dir, ignore_errors=True)
    os.makedirs(clone_dir, exist_ok=True)

    repo_url = source.repo_url
    # Prefer new access_token_encrypted, fall back to legacy github_token_encrypted
    token_encrypted = source.access_token_encrypted or source.github_token_encrypted
    if token_encrypted:
        token = decrypt_secret(token_encrypted)
        parsed = urlparse(repo_url)
        # oauth2:token works for both GitHub and GitLab
        repo_url = parsed._replace(netloc=f"oauth2:{token}@{parsed.netloc}").geturl()

    branch = source.branch or "main"

    def _clone():
        result = subprocess.run(
            ["git", "clone", "--branch", branch, "--depth", "1", repo_url, clone_dir],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git clone failed")
        return clone_dir

    return await asyncio.to_thread(_clone)


# ─── Code Tools ──────────────────────────────────────────────────────────────

_CODE_EXTENSIONS = (
    "*.py", "*.ts", "*.tsx", "*.js", "*.jsx", "*.go", "*.java", "*.sql",
    "*.md", "*.yaml", "*.yml", "*.sh", "*.txt", "*.json", "*.toml", "*.cfg",
    "*.ini", "*.rs", "*.rb", "*.php", "*.c", "*.cpp", "*.h", "*.cs",
)


async def grep_code(
    clone_path: str,
    pattern: str,
    file_glob: str | None = None,
    case_insensitive: bool = False,
    max_results: int = 50,
) -> str:
    """Search code files using grep. Returns file:line:match formatted output."""
    if not clone_path or not os.path.exists(clone_path):
        return "Repository not found on disk."

    cmd = ["grep", "-rn"]
    if case_insensitive:
        cmd.append("-i")

    if file_glob:
        cmd.extend(["--include", file_glob])
    else:
        for ext in _CODE_EXTENSIONS:
            cmd.extend(["--include", ext])

    cmd += [pattern, clone_path]

    def _run():
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:
        return f"grep error: {e}"

    if not result.stdout.strip():
        return "No matches found."

    lines = result.stdout.strip().split("\n")
    # Strip clone_path prefix for clean relative paths
    prefix = clone_path.rstrip("/") + "/"
    lines = [line.replace(prefix, "", 1) for line in lines if line]

    if len(lines) > max_results:
        truncated = len(lines) - max_results
        lines = lines[:max_results] + [f"... ({truncated} more matches — narrow your pattern or use file_glob)"]

    return "\n".join(lines)


async def read_code_file(
    clone_path: str,
    file_path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    """Read a file from the cloned repository with path traversal protection."""
    if not clone_path or not os.path.exists(clone_path):
        return "Repository not found on disk."

    base = Path(clone_path).resolve()
    target = (base / file_path).resolve()

    if not str(target).startswith(str(base) + os.sep) and target != base:
        return "Error: path traversal detected."

    if not target.exists():
        return f"File not found: {file_path}"

    if not target.is_file():
        return f"Not a file: {file_path}"

    def _read():
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()

    lines = await asyncio.to_thread(_read)
    total_lines = len(lines)

    start = max(1, start_line)
    end = min(end_line, total_lines) if end_line else total_lines
    selected = lines[start - 1:end]

    content = "".join(selected)
    if len(content) > 50_000:
        content = content[:50_000] + f"\n... [truncated at 50KB; file has {total_lines} lines total]"

    return f"# {file_path} (lines {start}-{min(end, total_lines)} of {total_lines})\n\n{content}"


async def list_code_files(
    clone_path: str,
    path: str = "",
    extension: str | None = None,
) -> str:
    """List code files in the cloned repository, optionally filtered by subdirectory or extension."""
    if not clone_path or not os.path.exists(clone_path):
        return "Repository not found on disk."

    base = Path(clone_path).resolve()
    target = (base / path).resolve() if path else base

    if not str(target).startswith(str(base)):
        return "Error: path traversal detected."

    if not target.exists():
        return f"Directory not found: {path or '/'}"

    code_exts = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".sql",
        ".md", ".yaml", ".yml", ".sh", ".txt", ".json", ".toml", ".cfg",
        ".ini", ".rs", ".rb", ".php", ".c", ".cpp", ".h", ".cs",
    }

    if extension:
        filter_ext = extension if extension.startswith(".") else f".{extension}"
        code_exts = {filter_ext}

    def _walk():
        results = []
        for root, dirs, files in os.walk(target):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("node_modules", "__pycache__", "dist", "build", ".git", "vendor")
            ]
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext not in code_exts:
                    continue
                fpath = os.path.join(root, fname)
                results.append(os.path.relpath(fpath, base))
        return sorted(results)

    results = await asyncio.to_thread(_walk)

    if not results:
        return "No matching files found."

    if len(results) > 200:
        truncated = len(results) - 200
        results = results[:200] + [f"... ({truncated} more files — use path= or extension= to filter)"]

    return "\n".join(results)


async def git_log(
    clone_path: str,
    max_commits: int = 20,
    file_path: str | None = None,
) -> str:
    """Get recent git commit history, optionally filtered to a specific file."""
    if not clone_path or not os.path.exists(clone_path):
        return "Repository not found on disk."

    cmd = [
        "git", "-C", clone_path, "log",
        f"--max-count={max_commits}",
        "--pretty=format:%h  %ad  %an: %s",
        "--date=short",
    ]
    if file_path:
        cmd += ["--", file_path]

    def _run():
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:
        return f"git log error: {e}"

    return result.stdout.strip() or "No commits found."


# ─── Document Access ─────────────────────────────────────────────────────────

async def read_knowledge_document(name: str, user: User, db: AsyncSession) -> str:
    """Return the full text of a knowledge document by name (case-insensitive prefix match).

    Reads directly from disk — no chunking, no vector DB.
    """
    # Resolve accessible sources
    if user.role == "admin":
        result = await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.type == "document",
                KnowledgeSource.file_path.isnot(None),
            )
        )
        sources = result.scalars().all()
    else:
        result = await db.execute(
            select(KnowledgeSource)
            .join(UserKnowledgePermission, KnowledgeSource.id == UserKnowledgePermission.knowledge_id)
            .where(
                UserKnowledgePermission.user_id == user.id,
                KnowledgeSource.type == "document",
                KnowledgeSource.file_path.isnot(None),
            )
        )
        sources = result.scalars().all()

    if not sources:
        return "No knowledge documents available."

    # Match by name (exact, then prefix, then substring — case-insensitive)
    name_lower = name.lower().strip()
    matched = None
    for strategy in ("exact", "prefix", "contains"):
        for src in sources:
            src_lower = src.name.lower().replace(".md", "").replace(".txt", "")
            if strategy == "exact" and src_lower == name_lower:
                matched = src
            elif strategy == "prefix" and src_lower.startswith(name_lower):
                matched = src
            elif strategy == "contains" and name_lower in src_lower:
                matched = src
        if matched:
            break

    if not matched:
        available = ", ".join(f'"{s.name}"' for s in sources)
        return f'Document "{name}" not found. Available: {available}'

    if not matched.file_path or not os.path.exists(matched.file_path):
        return f'File for "{matched.name}" is missing on disk.'

    with open(matched.file_path, "r", encoding="utf-8") as f:
        content = f.read()

    return f"# {matched.name}\n\n{content}"


async def list_knowledge_documents(user: User, db: AsyncSession) -> list[dict]:
    """List all readable knowledge documents with their names."""
    if user.role == "admin":
        result = await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.type == "document",
                KnowledgeSource.file_path.isnot(None),
            )
        )
    else:
        result = await db.execute(
            select(KnowledgeSource)
            .join(UserKnowledgePermission, KnowledgeSource.id == UserKnowledgePermission.knowledge_id)
            .where(
                UserKnowledgePermission.user_id == user.id,
                KnowledgeSource.type == "document",
                KnowledgeSource.file_path.isnot(None),
            )
        )
    sources = result.scalars().all()
    return [{"name": s.name.replace(".md", "").replace(".txt", ""), "description": ""} for s in sources]
