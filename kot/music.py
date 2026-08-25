from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

MusicAction = Literal[
    "play",
    "pause",
    "next",
    "previous",
    "volume_up",
    "volume_down",
    "now_playing",
]


@dataclass(frozen=True)
class MusicSnapshot:
    available: bool
    status: str | None
    playing: bool
    track: str | None
    artist: str | None
    volume: int | None


@dataclass(frozen=True)
class MusicResult:
    action: MusicAction | None
    snapshot: MusicSnapshot
    reply_text: str | None = None

    @property
    def playing(self) -> bool:
        return self.snapshot.playing


def parse_music_command(text: str) -> MusicAction | None:
    """Map a recognized Russian command to a small, explicit playerctl action."""

    normalized = " ".join(text.lower().replace("ё", "е").split())

    if any(
        phrase in normalized
        for phrase in (
            "что играет",
            "что сейчас играет",
            "какая песня",
            "какой трек",
            "что за песня",
            "что за трек",
        )
    ):
        return "now_playing"

    if any(
        phrase in normalized
        for phrase in (
            "следующий трек",
            "следующую песню",
            "следующая песня",
            "следующий",
            "переключи трек",
            "дальше музыку",
        )
    ):
        return "next"

    if any(
        phrase in normalized
        for phrase in (
            "предыдущий трек",
            "предыдущую песню",
            "предыдущая песня",
            "предыдущий",
            "верни трек",
            "верни песню",
        )
    ):
        return "previous"

    if any(
        phrase in normalized
        for phrase in (
            "сделай громче",
            "музыку громче",
            "громче",
            "прибавь громкость",
            "увеличь громкость",
        )
    ):
        return "volume_up"

    if any(
        phrase in normalized
        for phrase in (
            "сделай тише",
            "музыку тише",
            "тише",
            "убавь громкость",
            "уменьши громкость",
        )
    ):
        return "volume_down"

    if any(
        phrase in normalized
        for phrase in (
            "поставь на паузу",
            "поставь музыку на паузу",
            "пауза",
            "останови музыку",
            "выключи музыку",
            "стоп музыка",
        )
    ):
        return "pause"

    if any(
        phrase in normalized
        for phrase in (
            "продолжи музыку",
            "возобнови музыку",
            "включи музыку",
            "играй музыку",
            "продолжай музыку",
        )
    ):
        return "play"

    return None


class PlayerController:
    """Thin playerctl wrapper for the Chromium/Yandex Music MPRIS player."""

    def __init__(self) -> None:
        self.binary = os.getenv("KOT_PLAYERCTL_BIN", "playerctl")
        self.player = os.getenv("KOT_PLAYERCTL_PLAYER", "").strip()
        self.timeout = float(os.getenv("KOT_PLAYERCTL_TIMEOUT", "2.0"))
        self.volume_step = float(os.getenv("KOT_VOLUME_STEP", "0.05"))
        self._warned = False

    def _command(self, *args: str) -> list[str]:
        command = [self.binary]
        if self.player:
            command.extend(["--player", self.player])
        command.extend(args)
        return command

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()

        # A systemd system service does not inherit the desktop D-Bus variables.
        # Chromium exposes MPRIS on the khadas user's session bus, so reconstruct
        # the standard paths when they are absent.
        uid = os.getuid()
        runtime_dir = f"/run/user/{uid}"
        env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
        return env

    def _run(self, *args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            result = subprocess.run(
                self._command(*args),
                env=self._environment(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if not self._warned:
                print(f"[MUSIC] playerctl недоступен: {exc}")
                self._warned = True
            return None

        if result.returncode != 0:
            if not self._warned:
                details = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or f"code={result.returncode}"
                )
                print(f"[MUSIC] playerctl: {details}")
                self._warned = True
            return None

        self._warned = False
        return result

    def status(self) -> str | None:
        result = self._run("status")
        if result is None:
            return None
        return result.stdout.strip() or None

    def metadata(self) -> tuple[str | None, str | None]:
        result = self._run("metadata", "--format", "{{title}}\t{{artist}}")
        if result is None:
            return None, None

        line = result.stdout.strip()
        if not line:
            return None, None

        title, separator, artist = line.partition("\t")
        if not separator:
            return title.strip() or None, None
        return title.strip() or None, artist.strip() or None

    def volume(self) -> int | None:
        result = self._run("volume")
        if result is None:
            return None
        try:
            value = float(result.stdout.strip())
        except ValueError:
            return None
        return max(0, min(100, round(value * 100)))

    def snapshot(self) -> MusicSnapshot:
        status = self.status()
        if status is None:
            return MusicSnapshot(
                available=False,
                status=None,
                playing=False,
                track=None,
                artist=None,
                volume=None,
            )

        track, artist = self.metadata()
        return MusicSnapshot(
            available=True,
            status=status,
            playing=status == "Playing",
            track=track,
            artist=artist,
            volume=self.volume(),
        )

    def is_playing(self) -> bool:
        return self.status() == "Playing"

    def pause(self) -> bool:
        return self._run("pause") is not None

    def play(self) -> bool:
        return self._run("play") is not None

    def next(self) -> bool:
        return self._run("next") is not None

    def previous(self) -> bool:
        return self._run("previous") is not None

    def set_volume(self, percent: int) -> bool:
        normalized = max(0, min(100, percent)) / 100.0
        return self._run("volume", f"{normalized:.3f}") is not None

    def change_volume(self, delta: float) -> bool:
        current = self.volume()
        if current is None:
            return False
        target = current / 100.0 + delta
        return self.set_volume(round(max(0.0, min(1.0, target)) * 100))

    def pause_for_wake(self, minimum_pause: float = 0.0) -> bool:
        """Pause only when music was actually playing; return that previous state."""

        was_playing = self.is_playing()
        if was_playing and self.pause():
            self._resume_not_before = time.monotonic() + max(0.0, minimum_pause)
            print("[MUSIC] Пауза на время команды")
        return was_playing

    def _restore_playback(self) -> bool:
        resume_not_before = getattr(self, "_resume_not_before", 0.0)
        remaining = resume_not_before - time.monotonic()
        if remaining > 0:
            print(f"[MUSIC] Минимальная пауза: ещё {remaining:.2f} c")
            time.sleep(remaining)
        return self.play()

    @staticmethod
    def _now_playing_reply(snapshot: MusicSnapshot) -> str:
        if snapshot.artist and snapshot.track:
            return f"{snapshot.artist} — {snapshot.track}"
        if snapshot.track:
            return snapshot.track
        return "Не вижу название трека"

    def finish_command(self, text: str, was_playing: bool) -> MusicResult:
        """Execute a music command, or resume music paused only for wake/ASR."""

        action = parse_music_command(text)
        reply_text: str | None = None

        if action == "pause":
            self.pause()
            print("[MUSIC] pause")
        elif action == "play":
            self.play()
            print("[MUSIC] play")
        elif action == "next":
            self.next()
            print("[MUSIC] next")
            if was_playing:
                self._restore_playback()
        elif action == "previous":
            self.previous()
            print("[MUSIC] previous")
            if was_playing:
                self._restore_playback()
        elif action == "volume_up":
            self.change_volume(self.volume_step)
            print("[MUSIC] volume up")
            if was_playing:
                self._restore_playback()
        elif action == "volume_down":
            self.change_volume(-self.volume_step)
            print("[MUSIC] volume down")
            if was_playing:
                self._restore_playback()
        elif action == "now_playing":
            snapshot = self.snapshot()
            reply_text = self._now_playing_reply(snapshot)
            if was_playing:
                self._restore_playback()
        elif was_playing:
            # The music was paused only to improve ASR. For a non-music command,
            # restore the exact pre-wake intent: it should keep playing.
            self._restore_playback()
            print("[MUSIC] Возобновление после команды")

        snapshot = self.snapshot()
        if action in {"volume_up", "volume_down"} and snapshot.volume is not None:
            reply_text = f"Громкость {snapshot.volume}%"

        return MusicResult(action=action, snapshot=snapshot, reply_text=reply_text)

    def resume_after_timeout(self, was_playing: bool) -> MusicSnapshot:
        if was_playing:
            self._restore_playback()
            print("[MUSIC] Возобновление после timeout")
        return self.snapshot()
