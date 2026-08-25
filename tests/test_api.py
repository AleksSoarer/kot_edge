from pathlib import Path

from fastapi.testclient import TestClient

from kot.main import STATIC_DIR, app
from kot.ui import ACK_TEXT, MODE_UI

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_idle_presentation_comes_from_ui_module() -> None:
    response = client.post("/api/mode/idle")
    assert response.status_code == 200
    data = response.json()
    ui = MODE_UI["idle"]
    assert data["face"] == ui.face
    assert data["mode_title"] == ui.title
    assert data["mode_icon"] == ui.icon
    assert data["message"] == ui.message


def test_mode_change_updates_complete_presentation() -> None:
    response = client.post("/api/mode/listening")
    assert response.status_code == 200
    data = response.json()
    ui = MODE_UI["listening"]
    assert data["mode"] == "listening"
    assert data["face"] == ui.face
    assert data["mode_title"] == ui.title
    assert data["mode_icon"] == ui.icon
    assert data["message"] == ui.message


def test_status_can_be_changed() -> None:
    response = client.patch(
        "/api/status",
        json={
            "mode": "music",
            "track": "Last Ride",
            "artist": "Rodolphe Biker",
            "volume": 42,
            "core_online": True,
            "ha_online": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "music"
    assert data["message"] == MODE_UI["music"].message
    assert data["music_playing"] is True
    assert data["track"] == "Last Ride"


def test_heard_text_can_be_published() -> None:
    response = client.patch(
        "/api/status",
        json={"mode": "listening", "heard_text": "включи свет"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "listening"
    assert data["heard_text"] == "включи свет"


def test_reply_can_be_published() -> None:
    response = client.patch("/api/status", json={"reply_text": ACK_TEXT})
    assert response.status_code == 200
    assert response.json()["reply_text"] == ACK_TEXT


def test_dynamic_mode_text_is_not_duplicated_in_html_or_js() -> None:
    index = (STATIC_DIR / "index.html").read_text()
    app_js = (STATIC_DIR / "app.js").read_text()

    assert MODE_UI["idle"].message not in index
    assert MODE_UI["idle"].title not in index
    assert "const FACES" not in app_js
    assert "const TITLES" not in app_js
    assert "const ICONS" not in app_js


def test_ui_files_are_not_cached() -> None:
    root = client.get("/")
    js = client.get("/static/app.js")
    assert "no-store" in root.headers["cache-control"]
    assert "no-store" in js.headers["cache-control"]


def test_project_layout_uses_kot_package() -> None:
    assert Path(STATIC_DIR).parent.name == "kot"


def test_optional_music_metadata_can_be_cleared() -> None:
    client.patch(
        "/api/status",
        json={
            "track": "Old track",
            "artist": "Old artist",
            "volume": 50,
            "music_status": "Playing",
            "music_available": True,
        },
    )
    response = client.patch(
        "/api/status",
        json={
            "track": None,
            "artist": None,
            "volume": None,
            "music_status": None,
            "music_available": False,
        },
    )
    data = response.json()
    assert data["track"] is None
    assert data["artist"] is None
    assert data["volume"] is None
    assert data["music_status"] is None
    assert data["music_available"] is False


def test_music_sync_does_not_interrupt_listening_mode() -> None:
    import asyncio

    from kot.main import StateStore
    from kot.music import MusicSnapshot

    async def scenario() -> None:
        local_store = StateStore()
        await local_store.set_mode("listening")
        await local_store.sync_music(
            MusicSnapshot(
                available=True,
                status="Paused",
                playing=False,
                track="Track",
                artist="Artist",
                volume=25,
            )
        )
        status = await local_store.get()
        assert status.mode == "listening"
        assert status.track == "Track"
        assert status.volume == 25

    asyncio.run(scenario())
