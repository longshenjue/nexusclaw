"""
Sandbox execution server — runs inside each sandbox Docker container.

Manages one Jupyter kernel per container lifecycle:
- POST /initialize  — start kernel with DB env vars, called once per conversation assignment
- POST /execute     — execute Python code in the kernel, return stdout + /output/ artifacts
- POST /reset       — shutdown kernel + clean /output/, ready for next conversation
- GET  /health      — liveness check
"""

import asyncio
import base64
import logging
import mimetypes
import os
import shutil
import time
from pathlib import Path
from queue import Empty
from typing import Any

import jupyter_client
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sandbox")

OUTPUT_DIR = Path("/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="NexusClaw Sandbox")

# ── state ────────────────────────────────────────────────────────────────────

_km: jupyter_client.KernelManager | None = None
_kc: jupyter_client.BlockingKernelClient | None = None
_initialized = False
_exec_lock = asyncio.Lock()  # only one execution at a time per container


# ── request / response models ─────────────────────────────────────────────────

class InitRequest(BaseModel):
    env_vars: dict[str, str] = {}   # DS_name=mysql+pymysql://user:pw@host/db


class ExecuteRequest(BaseModel):
    code: str
    timeout: int = 600              # seconds


class ArtifactItem(BaseModel):
    name: str
    mime_type: str
    data_b64: str
    size_bytes: int


class ExecuteResponse(BaseModel):
    exit_code: int                  # 0 = success, 1 = error in code
    stdout: str
    stderr: str
    elapsed_ms: int
    artifacts: list[ArtifactItem]


# ── helpers ───────────────────────────────────────────────────────────────────

def _clean_output() -> None:
    """Remove all files from /output/ before each execution."""
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _collect_artifacts() -> list[ArtifactItem]:
    """Base64-encode all files written to /output/ and return as artifacts."""
    items: list[ArtifactItem] = []
    for path in sorted(OUTPUT_DIR.iterdir()):
        if not path.is_file():
            continue
        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type is None:
            mime_type = "application/octet-stream"
        data = path.read_bytes()
        items.append(ArtifactItem(
            name=path.name,
            mime_type=mime_type,
            data_b64=base64.b64encode(data).decode(),
            size_bytes=len(data),
        ))
    return items


def _collect_outputs(kc: jupyter_client.BlockingKernelClient, msg_id: str, timeout: int) -> dict[str, Any]:
    """
    Poll iopub channel until execution_state=idle, collecting all output.
    Returns dict with stdout, stderr, traceback, exit_code.
    """
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    traceback_parts: list[str] = []
    deadline = time.monotonic() + timeout

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "stdout": "".join(stdout_parts),
                "stderr": "".join(stderr_parts) or "Execution timed out",
                "exit_code": 1,
            }
        try:
            msg = kc.get_iopub_msg(timeout=min(remaining, 2.0))
        except Empty:
            continue

        msg_type = msg["msg_type"]
        content = msg.get("content", {})

        if msg_type == "stream":
            if content.get("name") == "stdout":
                stdout_parts.append(content.get("text", ""))
            elif content.get("name") == "stderr":
                stderr_parts.append(content.get("text", ""))

        elif msg_type == "error":
            traceback_parts.extend(content.get("traceback", []))
            stderr_parts.append(content.get("evalue", ""))

        elif msg_type == "status" and content.get("execution_state") == "idle":
            # Check if this idle corresponds to our message
            parent_id = msg.get("parent_header", {}).get("msg_id", "")
            if parent_id == msg_id:
                break

    # Strip ANSI escape codes from traceback for cleaner output
    import re
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    tb_clean = [ansi.sub("", line) for line in traceback_parts]

    has_error = bool(traceback_parts)
    return {
        "stdout": "".join(stdout_parts),
        "stderr": "\n".join(tb_clean) if tb_clean else "".join(stderr_parts),
        "exit_code": 1 if has_error else 0,
    }


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "initialized": _initialized}


@app.post("/initialize")
async def initialize(req: InitRequest) -> dict:
    """Start a Jupyter kernel with the given environment variables."""
    global _km, _kc, _initialized

    if _initialized:
        logger.warning("initialize called on already-initialized container — resetting first")
        await _do_reset()

    # Build env: inherit base env + inject datasource URLs
    env = {**os.environ, **req.env_vars}

    logger.info("Starting kernel with %d env vars", len(req.env_vars))
    km = jupyter_client.KernelManager(kernel_name="python3")
    km.start_kernel(env=env)

    kc = km.blocking_client()
    kc.start_channels()

    try:
        kc.wait_for_ready(timeout=30)
    except RuntimeError as e:
        km.shutdown_kernel(now=True)
        raise HTTPException(status_code=500, detail=f"Kernel failed to start: {e}")

    # Pre-import common packages so first execution is faster
    warmup_code = """
import os, sys
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # non-interactive backend — required in headless containers
import matplotlib.pyplot as plt
import sqlalchemy
"""
    msg_id = kc.execute(warmup_code, silent=True)
    _collect_outputs(kc, msg_id, timeout=60)

    _km = km
    _kc = kc
    _initialized = True
    logger.info("Kernel ready")
    return {"status": "initialized"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest) -> ExecuteResponse:
    """Execute Python code in the running kernel."""
    if not _initialized or _kc is None:
        raise HTTPException(status_code=400, detail="Container not initialized. Call /initialize first.")

    async with _exec_lock:
        _clean_output()
        t0 = time.monotonic()

        loop = asyncio.get_running_loop()
        msg_id = await loop.run_in_executor(None, lambda: _kc.execute(req.code))

        result = await loop.run_in_executor(
            None,
            lambda: _collect_outputs(_kc, msg_id, timeout=req.timeout),
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        artifacts = _collect_artifacts()

        # ── detailed execution log ────────────────────────────────────────
        code_lines = req.code.strip().splitlines()
        code_preview = "\n".join(code_lines[:10])
        if len(code_lines) > 10:
            code_preview += f"\n... ({len(code_lines) - 10} more lines)"

        if result["exit_code"] == 0:
            stdout_preview = result["stdout"][:500] if result["stdout"] else "(no stdout)"
            artifact_info = ", ".join(
                f"{a.name} ({a.mime_type}, {a.size_bytes}B)" for a in artifacts
            ) or "none"
            logger.info(
                "execute OK | elapsed=%dms stdout=%d chars artifacts=[%s]\n"
                "── code ──\n%s\n"
                "── stdout ──\n%s",
                elapsed_ms, len(result["stdout"]), artifact_info,
                code_preview, stdout_preview,
            )
        else:
            stderr_preview = result["stderr"][:800] if result["stderr"] else "(no stderr)"
            logger.error(
                "execute FAIL | elapsed=%dms exit=%d\n"
                "── code ──\n%s\n"
                "── stderr ──\n%s",
                elapsed_ms, result["exit_code"],
                code_preview, stderr_preview,
            )

        return ExecuteResponse(
            exit_code=result["exit_code"],
            stdout=result["stdout"],
            stderr=result["stderr"],
            elapsed_ms=elapsed_ms,
            artifacts=artifacts,
        )


@app.post("/reset")
async def reset() -> dict:
    """Shutdown kernel and clean state so this container can serve a new conversation."""
    await _do_reset()
    return {"status": "reset"}


async def _do_reset() -> None:
    global _km, _kc, _initialized
    if _km is not None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _km.shutdown_kernel(now=True))
        except Exception as e:
            logger.warning("Error shutting down kernel: %s", e)
        _km = None
        _kc = None
    _initialized = False
    _clean_output()
    logger.info("Container reset — ready for new conversation")
