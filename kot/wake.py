from __future__ import annotations

import json
import queue
import shlex
import subprocess
import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class WakeEvent:
    detected: bool
    score: float | None = None


def parse_runner_event(line: str) -> WakeEvent | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "wake" not in payload:
        return None
    score = payload.get("score")
    return WakeEvent(bool(payload["wake"]), float(score) if score is not None else None)


class NpuWakeDetector:
    """Bridge to a persistent VIM3 NPU process.

    The runner reads mono 16 kHz signed little-endian PCM from stdin and writes
    JSON lines to stdout, for example {"wake": true, "score": 0.91}.
    """

    def __init__(self, command: str) -> None:
        if not command.strip():
            raise ValueError("KOT_NPU_WAKE_COMMAND is required for the npu backend")
        self._events: queue.SimpleQueue[WakeEvent] = queue.SimpleQueue()
        self._process = subprocess.Popen(
            shlex.split(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None, bufsize=0,
        )
        self._reader = threading.Thread(target=self._read_events, daemon=True)
        self._reader.start()

    def _read_events(self) -> None:
        assert self._process.stdout is not None
        for raw_line in self._process.stdout:
            event = parse_runner_event(raw_line.decode("utf-8", errors="replace"))
            if event is not None:
                self._events.put(event)

    def accept(self, samples: np.ndarray) -> WakeEvent | None:
        if self._process.poll() is not None:
            raise RuntimeError(f"NPU wake runner stopped: code={self._process.returncode}")
        assert self._process.stdin is not None
        pcm = np.clip(samples, -1.0, 1.0)
        self._process.stdin.write((pcm * 32767.0).astype("<i2").tobytes())
        try:
            return self._events.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
