from __future__ import annotations

import math
import os
import time
from collections import Counter, deque

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - the controller runs on Linux
    termios = None
    tty = None


HOTMAP_HEADER = b"\xff" * 16
HOTMAP_SIZE = 16 * 16
BEAM_COMMANDS = b"0123456789AB"


class HotmapParser:
    """Incrementally extract 16x16 MA-USB8 hotmap payloads."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        frames: list[bytes] = []
        frame_size = len(HOTMAP_HEADER) + HOTMAP_SIZE
        while True:
            start = self.buffer.find(HOTMAP_HEADER)
            if start < 0:
                del self.buffer[: max(0, len(self.buffer) - len(HOTMAP_HEADER) + 1)]
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < frame_size:
                break
            frames.append(bytes(self.buffer[len(HOTMAP_HEADER) : frame_size]))
            del self.buffer[:frame_size]
        return frames


def hotmap_sector(
    payload: bytes,
    *,
    min_contrast: int = 12,
    clockwise: bool = True,
    offset: int = 0,
) -> int | None:
    """Return a 30-degree sector, clockwise from the top of the hotmap."""

    if len(payload) != HOTMAP_SIZE:
        return None
    low = min(payload)
    high = max(payload)
    if high - low < min_contrast:
        return None

    threshold = low + (high - low) * 0.75
    total = x_sum = y_sum = 0.0
    for index, value in enumerate(payload):
        weight = max(0.0, value - threshold)
        if not weight:
            continue
        x_sum += (index % 16 - 7.5) * weight
        y_sum += (7.5 - index // 16) * weight
        total += weight
    if total == 0:
        return None

    angle = math.atan2(x_sum / total, y_sum / total)
    if not clockwise:
        angle = -angle
    # ``round`` uses bankers rounding, so an angle exactly halfway between
    # sectors 0 and 1 would incorrectly remain in sector 0.  The controller's
    # sectors are ordinary half-up 30-degree bins.
    sector = math.floor((angle % (2 * math.pi)) / (math.pi / 6) + 0.5)
    return (sector + offset) % 12


class MicArrayLeds:
    """MA-USB8 LED control and optional hotmap-driven beam steering."""

    def __init__(
        self,
        device: str,
        enabled: bool = True,
        *,
        auto_beam: bool = False,
        beam_offset: int = 0,
        beam_clockwise: bool = True,
        stable_frames: int = 3,
        min_contrast: int = 12,
        no_frame_warning_seconds: float = 5.0,
    ) -> None:
        self.device = device
        self.enabled = enabled
        self.auto_beam = auto_beam
        self.beam_offset = beam_offset
        self.beam_clockwise = beam_clockwise
        self.stable_frames = max(1, stable_frames)
        self.min_contrast = min_contrast
        self.no_frame_warning_seconds = max(0.0, no_frame_warning_seconds)
        self._warned = False
        self._no_frames_warned = False
        self._descriptor: int | None = None
        self._parser = HotmapParser()
        self._candidates: deque[int | None] = deque(maxlen=self.stable_frames)
        self._sector: int | None = None
        self._candidate_sector: int | None = None
        self._voted_sector: int | None = None
        self._locked = False
        self._frames_total = 0
        self._frames_since_open = 0
        self._opened_at: float | None = None
        self._last_frame_at: float | None = None
        self._last_contrast: int | None = None

    @property
    def sector(self) -> int | None:
        return self._sector

    def _reset_connection_state(self, opened_at: float | None = None) -> None:
        """Discard state which cannot be trusted across a CDC reconnect."""

        self._parser = HotmapParser()
        self._candidates.clear()
        self._sector = None
        self._candidate_sector = None
        self._voted_sector = None
        self._frames_since_open = 0
        self._opened_at = opened_at
        self._last_frame_at = None
        self._last_contrast = None
        self._no_frames_warned = False

    def _open(self, now: float | None = None) -> int:
        if self._descriptor is not None:
            return self._descriptor
        flags = os.O_RDWR | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
        descriptor = os.open(self.device, flags)
        if tty is not None and termios is not None:
            try:
                tty.setraw(descriptor, when=termios.TCSANOW)
            except (OSError, termios.error):
                # CDC ACM normally accepts raw mode; keep best-effort writes
                # working on unusual drivers and mocked descriptors.
                pass
        self._reset_connection_state(time.monotonic() if now is None else now)
        self._descriptor = descriptor
        return descriptor

    def _majority_candidate(self) -> int | None:
        """Return a strict majority from the complete sliding frame window."""

        if len(self._candidates) < self.stable_frames:
            return None
        counts = Counter(candidate for candidate in self._candidates if candidate is not None)
        if not counts:
            return None
        candidate, votes = counts.most_common(1)[0]
        if votes < self.stable_frames // 2 + 1:
            return None
        return candidate

    def _note_frame(self, payload: bytes, now: float) -> int | None:
        self._frames_total += 1
        self._frames_since_open += 1
        self._last_frame_at = now
        self._last_contrast = max(payload) - min(payload) if payload else None
        self._no_frames_warned = False
        candidate = hotmap_sector(
            payload,
            min_contrast=self.min_contrast,
            clockwise=self.beam_clockwise,
            offset=self.beam_offset,
        )
        self._candidate_sector = candidate
        return candidate

    def _warn_if_hotmap_stalled(self, now: float) -> None:
        if self.no_frame_warning_seconds <= 0 or self._no_frames_warned:
            return
        since = self._last_frame_at if self._last_frame_at is not None else self._opened_at
        if since is None or now - since < self.no_frame_warning_seconds:
            return
        age = now - since
        print(
            f"[MIC-ARRAY] auto beam включён, но кадры hotmap "
            f"не поступают {age:.1f} c"
        )
        self._no_frames_warned = True

    def diagnostics(self, now: float | None = None) -> dict[str, object]:
        """Return a snapshot suitable for a periodic journal/JSON report."""

        checked_at = time.monotonic() if now is None else now
        age = (
            None
            if self._last_frame_at is None
            else max(0.0, checked_at - self._last_frame_at)
        )
        return {
            "auto_beam": self.auto_beam,
            "connected": self._descriptor is not None,
            "locked": self._locked,
            "frames_total": self._frames_total,
            "frames_since_open": self._frames_since_open,
            "last_frame_age": age,
            "last_contrast": self._last_contrast,
            "candidate_sector": self._candidate_sector,
            "voted_sector": self._voted_sector,
            "commanded_sector": self._sector,
        }

    def diagnostic_summary(self, now: float | None = None) -> str:
        """Format one compact diagnostic line; the caller controls its cadence."""

        values = self.diagnostics(now)
        age = values["last_frame_age"]
        age_text = "-" if age is None else f"{age:.1f}s"

        def sector_text(value: object) -> str:
            if not isinstance(value, int):
                return "-"
            return BEAM_COMMANDS[value:value + 1].decode()

        contrast = values["last_contrast"]
        contrast_text = "-" if contrast is None else str(contrast)

        return (
            "hotmap "
            f"frames={values['frames_total']} "
            f"connection_frames={values['frames_since_open']} "
            f"age={age_text} contrast={contrast_text} "
            f"candidate={sector_text(values['candidate_sector'])} "
            f"voted={sector_text(values['voted_sector'])} "
            f"commanded={sector_text(values['commanded_sector'])} "
            f"device={'open' if values['connected'] else 'closed'}"
        )

    def _write(self, command: bytes) -> None:
        descriptor: int | None = None
        temporary = not self.auto_beam
        try:
            descriptor = self._open()
            os.write(descriptor, command)
            self._warned = False
        except OSError as exc:
            if not self._warned:
                print(f"[MIC-ARRAY] {self.device} недоступен: {exc}")
                self._warned = True
            self.close()
        finally:
            if temporary and descriptor is not None:
                self.close()

    def set_listening(self, listening: bool) -> None:
        self._locked = listening
        if listening:
            self._candidates.clear()
            self._voted_sector = None
        if not self.enabled:
            return
        self._write(b"e" if listening else b"E")

    def poll(self) -> int | None:
        """Consume available hotmaps and steer CH6 after a stable direction."""

        if not self.auto_beam:
            return self._sector
        now = time.monotonic()
        try:
            descriptor = self._open(now)
            # Bound work per audio window so a busy CDC stream cannot starve ASR.
            for _ in range(8):
                try:
                    chunk = os.read(descriptor, 4096)
                except BlockingIOError:
                    break
                if not chunk:
                    raise OSError("устройство вернуло пустой поток hotmap")
                for payload in self._parser.feed(chunk):
                    candidate = self._note_frame(payload, now)
                    if self._locked:
                        self._candidates.clear()
                        self._voted_sector = None
                        continue
                    self._candidates.append(candidate)
                    voted = self._majority_candidate()
                    self._voted_sector = voted
                    if voted is not None and voted != self._sector:
                        os.write(descriptor, bytes((BEAM_COMMANDS[voted],)))
                        self._sector = voted
                        label = BEAM_COMMANDS[voted:voted + 1].decode()
                        print(f"[MIC-BEAM] сектор {label}")
            self._warned = False
            self._warn_if_hotmap_stalled(now)
        except OSError as exc:
            if not self._warned:
                print(f"[MIC-ARRAY] {self.device} недоступен: {exc}")
                self._warned = True
            self.close()
        return self._sector

    def close(self) -> None:
        if self._descriptor is not None:
            try:
                os.close(self._descriptor)
            except OSError:
                pass
        self._descriptor = None
        self._reset_connection_state()
