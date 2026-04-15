"""
Code execution service — manages a pool of sandbox Docker containers, each running
a Jupyter kernel for stateful Python execution within a conversation session.

Pool parameters (from env):
  SANDBOX_IMAGE        default: claw-sandbox
  SANDBOX_NETWORK      default: nexusclaw_network
  SANDBOX_POOL_MIN     default: 3  (standby containers always kept warm)
  SANDBOX_POOL_MAX     default: 5  (max concurrent containers)
  SANDBOX_IDLE_TTL     default: 1800  (seconds before idle container is released)
"""

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.datasource import Datasource, UserDatasourcePermission
from app.models.user import User
from app.utils.security import decrypt_secret

logger = logging.getLogger(__name__)

# ── config ────────────────────────────────────────────────────────────────────

SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "claw-sandbox")
SANDBOX_NETWORK = os.environ.get("SANDBOX_NETWORK", "nexusclaw_network")
POOL_MIN = int(os.environ.get("SANDBOX_POOL_MIN", "3"))
POOL_MAX = int(os.environ.get("SANDBOX_POOL_MAX", "5"))
IDLE_TTL = int(os.environ.get("SANDBOX_IDLE_TTL", "1800"))   # 30 minutes
CONTAINER_MEMORY = 512 * 1024 * 1024                          # 512 MB
SANDBOX_PORT = 8888


# ── data classes ──────────────────────────────────────────────────────────────

@dataclass
class ExecutionArtifact:
    name: str
    mime_type: str
    data_b64: str
    size_bytes: int


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_ms: int
    artifacts: list[ExecutionArtifact] = field(default_factory=list)


@dataclass
class SandboxContainer:
    container_id: str
    container_name: str
    http_url: str                      # http://{name}:8888
    conversation_id: str | None = None
    initialized: bool = False          # /initialize already called
    last_used_at: float = field(default_factory=time.monotonic)


# ── container pool ────────────────────────────────────────────────────────────

class ContainerPool:
    """
    Maintains a pool of warm sandbox containers.

    - _standby: queue of containers ready to be assigned to a conversation
    - _active:  conv_id → SandboxContainer currently in use
    - _sem:     limits total concurrent containers to POOL_MAX
    - _lock:    protects _standby/_active mutations
    """

    def __init__(self) -> None:
        self._standby: asyncio.Queue[SandboxContainer] = asyncio.Queue()
        self._active: dict[str, SandboxContainer] = {}
        self._sem: asyncio.Semaphore = asyncio.Semaphore(POOL_MAX)
        self._lock: asyncio.Lock = asyncio.Lock()
        self._docker: Any = None          # aiodocker.Docker instance
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize the Docker client and pre-warm standby containers."""
        try:
            import aiodocker
            self._docker = aiodocker.Docker()
        except Exception as e:
            logger.error("Failed to connect to Docker daemon: %s", e)
            logger.warning("Code execution (run_python) will be unavailable")
            return

        self._running = True

        # Pre-warm POOL_MIN containers in parallel
        warm_tasks = [self._spawn_container() for _ in range(POOL_MIN)]
        results = await asyncio.gather(*warm_tasks, return_exceptions=True)
        ready = 0
        for r in results:
            if isinstance(r, SandboxContainer):
                await self._standby.put(r)
                ready += 1
            else:
                logger.warning("Failed to pre-warm sandbox container: %s", r)

        logger.info("ContainerPool started | standby=%d/%d image=%s", ready, POOL_MIN, SANDBOX_IMAGE)

        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self) -> None:
        """Stop all containers and the Docker client."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()

        if self._docker is None:
            return

        all_containers: list[SandboxContainer] = list(self._active.values())
        while not self._standby.empty():
            try:
                all_containers.append(self._standby.get_nowait())
            except asyncio.QueueEmpty:
                break

        for c in all_containers:
            await self._remove_container(c)

        await self._docker.close()
        logger.info("ContainerPool shutdown — removed %d containers", len(all_containers))

    # ── acquire / release ──────────────────────────────────────────────────

    async def acquire(self, conversation_id: str) -> SandboxContainer:
        """
        Return the container assigned to this conversation, or get one from standby.
        Blocks if POOL_MAX containers are all active (waits for a slot).
        """
        async with self._lock:
            if conversation_id in self._active:
                c = self._active[conversation_id]
                c.last_used_at = time.monotonic()
                return c

        # Need a new container — wait for semaphore slot
        await self._sem.acquire()

        async with self._lock:
            # Try standby first
            try:
                c = self._standby.get_nowait()
            except asyncio.QueueEmpty:
                # Spawn a new one (sem already acquired)
                try:
                    c = await self._spawn_container()
                except Exception as e:
                    self._sem.release()
                    raise RuntimeError(f"Failed to spawn sandbox container: {e}") from e

            c.conversation_id = conversation_id
            c.last_used_at = time.monotonic()
            self._active[conversation_id] = c
            logger.info("Container assigned | conv=%s container=%s", conversation_id[:8], c.container_name)
            return c

    async def release(self, conversation_id: str) -> None:
        """
        Return the container to standby: reset kernel, clean /output/.
        If standby is full (already at POOL_MIN), destroy the container instead.
        """
        async with self._lock:
            c = self._active.pop(conversation_id, None)
            if c is None:
                return

        logger.info("Releasing container | conv=%s container=%s", conversation_id[:8], c.container_name)

        try:
            await self._reset_container(c)
            async with self._lock:
                if self._standby.qsize() < POOL_MIN:
                    c.conversation_id = None
                    c.initialized = False
                    await self._standby.put(c)
                    self._sem.release()
                    return
        except Exception as e:
            logger.warning("Container reset failed, destroying: %s", e)

        await self._remove_container(c)
        self._sem.release()

        # Spawn a replacement to maintain standby size
        asyncio.create_task(self._replenish_standby())

    # ── internal helpers ───────────────────────────────────────────────────

    async def _spawn_container(self) -> SandboxContainer:
        """Create and start a new sandbox container."""
        name = f"claw-sandbox-{uuid.uuid4().hex[:8]}"
        config = {
            "Image": SANDBOX_IMAGE,
            "Hostname": name,
            "HostConfig": {
                "Memory": CONTAINER_MEMORY,
                "MemorySwap": CONTAINER_MEMORY,
                "NetworkMode": SANDBOX_NETWORK,
                "AutoRemove": False,
            },
            "NetworkingConfig": {
                "EndpointsConfig": {
                    SANDBOX_NETWORK: {"Aliases": [name]}
                }
            },
        }
        container = await self._docker.containers.create(config=config, name=name)
        await container.start()

        http_url = f"http://{name}:{SANDBOX_PORT}"

        # Wait until /health returns 200
        c = SandboxContainer(
            container_id=container.id,
            container_name=name,
            http_url=http_url,
        )
        await self._wait_healthy(c)
        logger.info("Container spawned | name=%s id=%s", name, container.id[:12])
        return c

    async def _wait_healthy(self, c: SandboxContainer, timeout: int = 30) -> None:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(f"{c.http_url}/health")
                    if r.status_code == 200:
                        return
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        raise TimeoutError(f"Sandbox container {c.container_name} did not become healthy in {timeout}s")

    async def _reset_container(self, c: SandboxContainer) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{c.http_url}/reset")

    async def _remove_container(self, c: SandboxContainer) -> None:
        try:
            container = await self._docker.containers.get(c.container_id)
            await container.stop(t=5)
            await container.delete()
        except Exception as e:
            logger.warning("Failed to remove container %s: %s", c.container_name, e)

    async def _replenish_standby(self) -> None:
        """Spawn one new standby container (called after a container is destroyed)."""
        try:
            c = await self._spawn_container()
            async with self._lock:
                await self._standby.put(c)
            logger.info("Standby replenished | standby=%d", self._standby.qsize())
        except Exception as e:
            logger.error("Failed to replenish standby: %s", e)

    async def _cleanup_loop(self) -> None:
        """Periodically release containers that have been idle for more than IDLE_TTL."""
        while self._running:
            await asyncio.sleep(60)
            try:
                now = time.monotonic()
                idle_convs = [
                    conv_id
                    for conv_id, c in list(self._active.items())
                    if now - c.last_used_at > IDLE_TTL
                ]
                for conv_id in idle_convs:
                    logger.info("Releasing idle container | conv=%s", conv_id[:8])
                    await self.release(conv_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Cleanup loop error: %s", e)

    @property
    def available(self) -> bool:
        return self._docker is not None and self._running


# ── module-level singleton ────────────────────────────────────────────────────

container_pool = ContainerPool()


# ── datasource credential resolution ─────────────────────────────────────────

async def _resolve_db_env_vars(
    datasource_ids: list[str],
    user: User,
    db: AsyncSession,
) -> dict[str, str]:
    """
    For each requested datasource_id, verify the user has access, decrypt
    the password, and return a dict of env vars:
      DS_{datasource_name} = mysql+pymysql://user:pass@host:port/db
    Datasources the user cannot access are silently skipped.
    """
    env_vars: dict[str, str] = {}

    for ds_id in datasource_ids:
        try:
            # Permission check (reusing the same pattern as mysql_service)
            result = await db.execute(
                select(Datasource).where(
                    Datasource.id == ds_id,
                    Datasource.is_active == True,
                )
            )
            datasource = result.scalar_one_or_none()
            if datasource is None:
                logger.warning("Datasource %s not found — skipping env injection", ds_id)
                continue

            if user.role != "admin":
                perm_result = await db.execute(
                    select(UserDatasourcePermission).where(
                        UserDatasourcePermission.user_id == user.id,
                        UserDatasourcePermission.datasource_id == ds_id,
                    )
                )
                if perm_result.scalar_one_or_none() is None:
                    logger.warning(
                        "User %s has no permission for datasource %s — skipping",
                        user.username, ds_id,
                    )
                    continue

            password = decrypt_secret(datasource.password_encrypted)
            # URL-encode password to handle special characters
            from urllib.parse import quote_plus
            conn_str = (
                f"mysql+pymysql://{datasource.username}:{quote_plus(password)}"
                f"@{datasource.host}:{datasource.port}/{datasource.database_name}"
            )
            # Sanitize name for use as an env var key
            safe_name = datasource.name.replace(" ", "_").replace("-", "_").upper()
            env_key = f"DS_{safe_name}"
            env_vars[env_key] = conn_str
            logger.debug("Injected datasource env var: %s → %s@%s/%s",
                         env_key, datasource.username, datasource.host, datasource.database_name)
        except Exception as e:
            logger.error("Failed to resolve datasource %s: %s", ds_id, e)

    return env_vars


# ── public API ────────────────────────────────────────────────────────────────

async def execute_python(
    code: str,
    conversation_id: str,
    datasource_ids: list[str],
    user: User,
    db: AsyncSession,
    timeout: int = 600,
) -> ExecutionResult:
    """
    Execute Python code in an isolated sandbox container for this conversation.

    - First call for a conversation: acquires a container, initializes kernel with DB env vars.
    - Subsequent calls in the same conversation: reuses the same container (stateful kernel).
    - Container is released automatically after IDLE_TTL seconds of inactivity.
    """
    if not container_pool.available:
        return ExecutionResult(
            stdout="",
            stderr="Code execution is unavailable: Docker sandbox not connected.",
            exit_code=1,
            elapsed_ms=0,
        )

    # Acquire container (blocks if pool is at POOL_MAX)
    container = await container_pool.acquire(conversation_id)

    # Initialize kernel on first use for this conversation
    if not container.initialized:
        env_vars = await _resolve_db_env_vars(datasource_ids, user, db)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{container.http_url}/initialize",
                json={"env_vars": env_vars},
            )
            resp.raise_for_status()
        container.initialized = True
        logger.info(
            "Sandbox initialized | conv=%s container=%s datasources=%d",
            conversation_id[:8], container.container_name, len(env_vars),
        )

    # Execute code
    async with httpx.AsyncClient(timeout=float(timeout + 30)) as client:
        try:
            resp = await client.post(
                f"{container.http_url}/execute",
                json={"code": code, "timeout": timeout},
                timeout=float(timeout + 30),
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            return ExecutionResult(
                stdout="",
                stderr=f"Execution timed out after {timeout}s",
                exit_code=1,
                elapsed_ms=timeout * 1000,
            )
        except Exception as e:
            return ExecutionResult(
                stdout="",
                stderr=f"Sandbox communication error: {e}",
                exit_code=1,
                elapsed_ms=0,
            )

    container.last_used_at = time.monotonic()

    artifacts = [
        ExecutionArtifact(
            name=a["name"],
            mime_type=a["mime_type"],
            data_b64=a["data_b64"],
            size_bytes=a["size_bytes"],
        )
        for a in data.get("artifacts", [])
    ]

    return ExecutionResult(
        stdout=data.get("stdout", ""),
        stderr=data.get("stderr", ""),
        exit_code=data.get("exit_code", 0),
        elapsed_ms=data.get("elapsed_ms", 0),
        artifacts=artifacts,
    )
