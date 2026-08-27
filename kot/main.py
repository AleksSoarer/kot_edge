from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .music import MusicSnapshot, PlayerController
from .ui import MODE_UI, Mode

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
REPLY_SECONDS = float(os.getenv("KOT_REPLY_SECONDS", "2.3"))
HEARD_SECONDS = float(os.getenv("KOT_HEARD_SECONDS", "12.0"))
MUSIC_MONITOR_INTERVAL = float(os.getenv("KOT_MUSIC_MONITOR_INTERVAL", "2.0"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mode_fields(mode: Mode) -> dict[str, str]:
    ui = MODE_UI[mode]
    return {
        "face": ui.face,
        "mode_title": ui.title,
        "mode_icon": ui.icon,
        "message": ui.message,
    }


class Status(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "idle"
    face: str = MODE_UI["idle"].face
    mode_title: str = MODE_UI["idle"].title
    mode_icon: str = MODE_UI["idle"].icon
    message: str = MODE_UI["idle"].message

    track: str | None = None
    artist: str | None = None
    volume: int | None = Field(default=None, ge=0, le=100)
    music_status: str | None = None
    music_available: bool = False
    heard_text: str | None = None
    reply_text: str | None = None

    mic_online: bool = True
    core_online: bool = False
    ha_online: bool = False
    music_playing: bool = False

    updated_at: str = Field(default_factory=utc_now)


class StatusPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode | None = None
    # Optional override for exceptional details. Standard mode text always comes from kot/ui.py.
    message: str | None = None
    track: str | None = None
    artist: str | None = None
    volume: int | None = Field(default=None, ge=0, le=100)
    music_status: str | None = None
    music_available: bool | None = None
    heard_text: str | None = None
    reply_text: str | None = None

    mic_online: bool | None = None
    core_online: bool | None = None
    ha_online: bool | None = None
    music_playing: bool | None = None


class StateStore:
    def __init__(self) -> None:
        self._status = Status()
        self._lock = asyncio.Lock()
        self._clients: set[WebSocket] = set()
        self._reply_generation = 0
        self._heard_generation = 0

    async def get(self) -> Status:
        async with self._lock:
            return self._status.model_copy(deep=True)

    async def update(self, patch: StatusPatch) -> Status:
        reply_generation: int | None = None
        heard_generation: int | None = None

        async with self._lock:
            data = self._status.model_dump()
            # exclude_unset is intentional: an explicit JSON null can clear stale
            # metadata, while omitted fields remain unchanged.
            changes = patch.model_dump(exclude_unset=True)

            if patch.mode is not None:
                data["mode"] = patch.mode
                data.update(mode_fields(patch.mode))

            for key, value in changes.items():
                if key != "mode":
                    data[key] = value

            data["updated_at"] = utc_now()

            if patch.mode == "music" and patch.music_playing is None:
                data["music_playing"] = True
            elif patch.mode in {"listening", "thinking", "speaking", "offline", "error"}:
                if patch.music_playing is None:
                    data["music_playing"] = False

            if patch.reply_text is not None:
                self._reply_generation += 1
                if patch.reply_text.strip():
                    reply_generation = self._reply_generation

            if patch.heard_text is not None:
                self._heard_generation += 1
                if patch.heard_text.strip():
                    heard_generation = self._heard_generation

            self._status = Status(**data)
            snapshot = self._status.model_copy(deep=True)

        await self.broadcast(snapshot)

        if reply_generation is not None:
            asyncio.create_task(
                self._clear_field_after("reply_text", reply_generation, REPLY_SECONDS)
            )
        if heard_generation is not None:
            asyncio.create_task(
                self._clear_field_after("heard_text", heard_generation, HEARD_SECONDS)
            )

        return snapshot

    async def sync_music(self, music: MusicSnapshot) -> Status:
        """Refresh MPRIS data without breaking LISTEN/THINK/SPEAK states."""

        async with self._lock:
            data = self._status.model_dump()
            previous = {
                "track": data["track"],
                "artist": data["artist"],
                "volume": data["volume"],
                "music_status": data["music_status"],
                "music_available": data["music_available"],
                "music_playing": data["music_playing"],
                "mode": data["mode"],
            }

            data["track"] = music.track
            data["artist"] = music.artist
            data["volume"] = music.volume
            data["music_status"] = music.status
            data["music_available"] = music.available
            data["music_playing"] = music.playing

            # Music monitoring owns IDLE <-> MUSIC only. A background poll must
            # never kick the cat out of LISTEN/THINK/SPEAK/ERROR.
            if data["mode"] in {"idle", "music"}:
                target_mode: Mode = "music" if music.playing else "idle"
                data["mode"] = target_mode
                data.update(mode_fields(target_mode))

            current = {
                "track": data["track"],
                "artist": data["artist"],
                "volume": data["volume"],
                "music_status": data["music_status"],
                "music_available": data["music_available"],
                "music_playing": data["music_playing"],
                "mode": data["mode"],
            }
            if current == previous:
                return self._status.model_copy(deep=True)

            data["updated_at"] = utc_now()
            self._status = Status(**data)
            snapshot = self._status.model_copy(deep=True)

        await self.broadcast(snapshot)
        return snapshot

    async def _clear_field_after(self, field: str, generation: int, delay: float) -> None:
        await asyncio.sleep(delay)

        async with self._lock:
            current_generation = (
                self._reply_generation if field == "reply_text" else self._heard_generation
            )
            if generation != current_generation or not getattr(self._status, field):
                return

            data = self._status.model_dump()
            data[field] = None
            data["updated_at"] = utc_now()
            self._status = Status(**data)
            snapshot = self._status.model_copy(deep=True)

        await self.broadcast(snapshot)

    async def set_mode(self, mode: Mode) -> Status:
        return await self.update(StatusPatch(mode=mode))

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        await websocket.send_json((await self.get()).model_dump())

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, status: Status) -> None:
        if not self._clients:
            return

        payload = status.model_dump()
        dead: list[WebSocket] = []
        for websocket in tuple(self._clients):
            try:
                await websocket.send_json(payload)
            except Exception:
                dead.append(websocket)

        for websocket in dead:
            self._clients.discard(websocket)


store = StateStore()


async def monitor_music() -> None:
    controller = PlayerController()
    while True:
        try:
            snapshot = await asyncio.to_thread(controller.snapshot)
            await store.sync_music(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[MUSIC-MONITOR] {exc}")
        await asyncio.sleep(MUSIC_MONITOR_INTERVAL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task: asyncio.Task[None] | None = None
    if MUSIC_MONITOR_INTERVAL > 0:
        task = asyncio.create_task(monitor_music())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Kot Edge", version="0.8.8", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_ui_cache(request: Request, call_next) -> Response:
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def require_token(x_kot_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("KOT_EDGE_TOKEN")
    if expected and x_kot_token != expected:
        raise HTTPException(status_code=401, detail="Invalid X-Kot-Token")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status", response_model=Status)
async def get_status() -> Status:
    return await store.get()


@app.patch("/api/status", response_model=Status, dependencies=[Depends(require_token)])
async def patch_status(patch: StatusPatch) -> Status:
    return await store.update(patch)


@app.post("/api/mode/{mode}", response_model=Status, dependencies=[Depends(require_token)])
async def set_mode(mode: Mode) -> Status:
    return await store.set_mode(mode)


@app.websocket("/ws")
async def websocket_status(websocket: WebSocket) -> None:
    await store.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        store.disconnect(websocket)
    except Exception:
        store.disconnect(websocket)
