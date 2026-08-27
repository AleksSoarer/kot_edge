from __future__ import annotations

import math
import os
from collections import deque

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
    return (round((angle % (2 * math.pi)) / (math.pi / 6)) + offset) % 12


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
    ) -> None:
        self.device = device
        self.enabled = enabled
        self.auto_beam = auto_beam
        self.beam_offset = beam_offset
        self.beam_clockwise = beam_clockwise
        self.stable_frames = max(1, stable_frames)
        self.min_contrast = min_contrast
        self._warned = False
        self._descriptor: int | None = None
        self._parser = HotmapParser()
        self._candidates: deque[int] = deque(maxlen=self.stable_frames)
        self._sector: int | None = None
        self._locked = False

    @property
    def sector(self) -> int | None:
        return self._sector

    def _open(self) -> int:
        if self._descriptor is not None:
            return self._descriptor
        flags = os.O_RDWR | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
        descriptor = os.open(self.device, flags)
        if tty is not None and termios is not None:
            try:
                tty.setraw(descriptor, when=termios.TCSANOW)
            except OSError:
                # CDC ACM normally accepts raw mode; keep best-effort writes
                # working on unusual drivers and mocked descriptors.
                pass
        self._descriptor = descriptor
        return descriptor

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
        if not self.enabled:
            return
        self._write(b"e" if listening else b"E")

    def poll(self) -> int | None:
        """Consume available hotmaps and steer CH6 after a stable direction."""

        if not self.auto_beam:
            return self._sector
        try:
            descriptor = self._open()
            # Bound work per audio window so a busy CDC stream cannot starve ASR.
            for _ in range(8):
                try:
                    chunk = os.read(descriptor, 4096)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                for payload in self._parser.feed(chunk):
                    if self._locked:
                        self._candidates.clear()
                        continue
                    candidate = hotmap_sector(
                        payload,
                        min_contrast=self.min_contrast,
                        clockwise=self.beam_clockwise,
                        offset=self.beam_offset,
                    )
                    if candidate is None:
                        self._candidates.clear()
                        continue
                    self._candidates.append(candidate)
                    if (
                        len(self._candidates) == self.stable_frames
                        and len(set(self._candidates)) == 1
                        and candidate != self._sector
                    ):
                        os.write(descriptor, bytes((BEAM_COMMANDS[candidate],)))
                        self._sector = candidate
                        label = BEAM_COMMANDS[candidate:candidate + 1].decode()
                        print(f"[MIC-BEAM] сектор {label}")
            self._warned = False
        except OSError as exc:
            if not self._warned:
                print(f"[MIC-ARRAY] {self.device} недоступен: {exc}")
                self._warned = True
            self.close()
        return self._sector

    def close(self) -> None:
        if self._descriptor is None:
            return
        try:
            os.close(self._descriptor)
        except OSError:
            pass
        self._descriptor = None
