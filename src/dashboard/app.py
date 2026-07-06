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
from .overrides import append_override, build_override_record

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class AnnotationIn(BaseModel):
    verdict: Literal["good", "wrong"]
    # Snapshots of what the page was DISPLAYING at tap time.
    state: dict
    recommendation: dict


class OverrideIn(BaseModel):
    action: Literal["skip", "wrong_vibe", "manual"]
    # Snapshots of what the page was DISPLAYING at tap time (the annotation
    # convention; played_through is controller-emitted, never POSTed).
    state: dict
    recommendation: dict
    now_playing: dict
    chosen: dict | None = None  # manual only: {"genre": ..., "tier": ...}


def create_app(
    bridge: DashboardBridge,
    annotations_dir: Path,
    overrides_dir: Path | None = None,
    playback=None,  # PlaybackController | None; None = shadow mode
    playlists_path: Path | None = None,  # curated mapping for the manual picker
) -> FastAPI:
    app = FastAPI(title="Read the Room — shadow dashboard")
    overrides_dir = overrides_dir or Path("data/overrides")

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

    @app.post("/overrides", status_code=201)
    def override(payload: OverrideIn) -> dict:
        """Capture the label, THEN act on it. A provider failure degrades
        playback but never loses the record. (Sync handler: FastAPI runs it
        on the threadpool, so provider I/O never blocks the event loop.)"""
        try:
            record = build_override_record(
                payload.action,
                payload.state,
                payload.recommendation,
                payload.now_playing,
                payload.chosen,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        path = append_override(overrides_dir, record)
        log.info("override %s -> %s", payload.action, path)

        acted_track = None
        action_error = None
        if playback is not None:
            from playback import ProviderError

            try:
                if payload.action == "skip":
                    acted_track = playback.skip()
                elif payload.action == "wrong_vibe":
                    acted_track = playback.wrong_vibe()
                elif payload.action == "manual":
                    acted_track = playback.manual_pick(
                        payload.chosen["genre"], payload.chosen["tier"]
                    )
            except (ProviderError, KeyError) as exc:
                action_error = str(exc) or type(exc).__name__
                log.warning("override %s recorded but action failed: %s",
                            payload.action, exc)
        return {
            "ok": True,
            "ts": record["ts"],
            "path": str(path),
            "action_ok": acted_track is not None,
            "acted_track": acted_track.to_dict() if acted_track else None,
            "action_error": action_error,
        }

    @app.get("/playlists")
    async def playlists() -> dict:
        """Mapped (genre, tier) pairs for the manual picker. Reloaded per
        request so curation edits show up without a restart."""
        if playlists_path is None:
            return {"mappings": []}
        from playback import load_playlists

        try:
            mapping = load_playlists(playlists_path)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {
            "mappings": [
                {"genre": genre, "tier": tier} for genre, tier in sorted(mapping)
            ]
        }

    return app
