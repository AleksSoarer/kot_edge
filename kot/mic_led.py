from __future__ import annotations

import os


class MicArrayLeds:
    """Best-effort MA-USB8 LED control over its CDC ACM serial device."""

    def __init__(self, device: str, enabled: bool = True) -> None:
        self.device = device
        self.enabled = enabled
        self._warned = False

    def set_listening(self, listening: bool) -> None:
        if not self.enabled:
            return
        descriptor: int | None = None
        try:
            flags = (
                os.O_WRONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOCTTY", 0)
            )
            descriptor = os.open(
                self.device,
                flags,
            )
            # This MA-USB8 firmware shows red D1 in the disabled (`e`) mode.
            # We use that as the confirmed-wake indicator. `E` restores the
            # normal blue sound-direction visualization.
            os.write(descriptor, b"e" if listening else b"E")
            self._warned = False
        except OSError as exc:
            if not self._warned:
                print(f"[MIC-LED] {self.device} недоступен: {exc}")
                self._warned = True
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
