"""Serve a transcript to the UI: GET /events (Server-Sent Events, follows the file live) and
GET /events.json (everything so far). CORS is open so a static HTML page can connect."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .transcript import load


def create_events_app(path: Path, poll_s: float = 0.25) -> FastAPI:
    path = Path(path)
    app = FastAPI(title="bASIC agent events")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "transcript": str(path), "exists": path.exists()}

    @app.get("/events.json")
    def events_json() -> list[dict]:
        return load(path) if path.exists() else []

    @app.get("/events")
    async def events() -> StreamingResponse:
        async def stream():
            pos = 0
            while True:
                if path.exists():
                    with path.open(encoding="utf-8") as f:
                        f.seek(pos)
                        for line in f:
                            if line.strip():
                                yield f"data: {json.dumps(json.loads(line))}\n\n"
                        pos = f.tell()
                await asyncio.sleep(poll_s)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
