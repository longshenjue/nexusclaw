"""WebSocket chat endpoint with streaming support."""
import asyncio
import json
import time
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError
from sqlalchemy import select

from app.database import get_db, AsyncSessionLocal
from app.models.user import User
from app.utils.security import decode_token
from app.services.chat_service import handle_chat_message

router = APIRouter(prefix="/chat", tags=["chat"])


async def get_user_from_token(token: str, db: AsyncSession) -> User | None:
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
        return result.scalar_one_or_none()
    except JWTError:
        return None


async def _keepalive(websocket: WebSocket, stop_event: asyncio.Event, progress: dict):
    """Send periodic heartbeats with task progress to keep the WebSocket alive."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            try:
                elapsed = int(time.monotonic() - progress["t_start"])
                await websocket.send_json({
                    "type": "heartbeat",
                    "status": progress["status"],
                    "iteration": progress["iteration"],
                    "elapsed": elapsed,
                })
            except Exception:
                break


@router.websocket("/ws/{conversation_id}")
async def chat_websocket(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    token: str = Query(...),
):
    await websocket.accept()

    async with AsyncSessionLocal() as db:
        user = await get_user_from_token(token, db)
        if not user:
            await websocket.send_json({"type": "error", "code": "unauthorized", "message": "Invalid token"})
            await websocket.close(code=1008)
            return

        await websocket.send_json({"type": "connected", "user": user.username})

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                    continue

                if data.get("type") == "message":
                    content = data.get("content", "").strip()
                    if not content:
                        continue

                    progress = {"status": "thinking", "iteration": 0, "t_start": time.monotonic()}
                    stop_event = asyncio.Event()
                    keepalive_task = asyncio.create_task(_keepalive(websocket, stop_event, progress))
                    try:
                        await handle_chat_message(
                            ws=websocket,
                            conversation_id=conversation_id,
                            user_message=content,
                            model_id_override=data.get("model_id"),
                            current_user=user,
                            db=db,
                            progress=progress,
                        )
                    finally:
                        stop_event.set()
                        keepalive_task.cancel()

                elif data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            import traceback
            import logging
            logging.getLogger("app.chat").error("ws_error | %s\n%s", e, traceback.format_exc())
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
