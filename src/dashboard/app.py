"""FastAPI app: static page, websocket state/recommendation stream, and the
annotation POST endpoint. Server logic stays thin — all presentation is in
static/index.html, all mapping logic in the Mapper."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .annotations import append_annotation, build_record
from .bridge import DashboardBridge

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class AnnotationIn(BaseModel):
    verdict: Literal["good", "wrong"]
    # Snapshots of what the page was DISPLAYING at tap time.
    state: dict
    recommendation: dict


def create_app(bridge: DashboardBridge, annotations_dir: Path) -> FastAPI:
    app = FastAPI(title="Read the Room — shadow dashboard")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        token = bridge.register(queue, asyncio.get_running_loop())
        try:
            # Replay: rolling history fills the timeline, then the current
            # recommendation, then live frames as they arrive.
            history, current_rec = bridge.snapshot()
            for frame in history:
                await websocket.send_json(frame)
            if current_rec is not None:
                await websocket.send_json(current_rec)
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            bridge.unregister(token)

    @app.post("/annotations", status_code=201)
    async def annotate(payload: AnnotationIn) -> dict:
        try:
            record = build_record(
                payload.verdict, payload.state, payload.recommendation
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        path = append_annotation(annotations_dir, record)
        log.info("annotation %s -> %s", payload.verdict, path)
        return {"ok": True, "ts": record["ts"], "path": str(path)}

    return app
