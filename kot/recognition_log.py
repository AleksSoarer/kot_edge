from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class RecognitionLogger:
    """Best-effort rotating JSONL event log for offline ASR analysis."""

    def __init__(
        self,
        path: Path | None,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        backups: int = 5,
        mode: str = "all",
    ) -> None:
        self.path = path
        self._logger: logging.Logger | None = None
        self.mode = mode.strip().lower()
        self.session_id = uuid.uuid4().hex
        self._sequence = 0
        self._warned = False
        if self.mode not in {"all", "accepted", "off"}:
            print(f"[ASR-LOG] Неизвестный режим {mode!r}; журнал отключён")
            self.mode = "off"
        if path is None or self.mode == "off":
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.Logger(f"kot.recognition.{id(self)}", level=logging.INFO)
            logger.propagate = False
            handler = RotatingFileHandler(
                path,
                maxBytes=max(1, max_bytes),
                backupCount=max(1, backups),
                encoding="utf-8",
            )
            try:
                path.chmod(0o600)
            except OSError:
                pass
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            self._logger = logger
        except OSError as exc:
            print(f"[ASR-LOG] Не удалось открыть {path}: {exc}")

    @property
    def enabled(self) -> bool:
        return self._logger is not None

    def event(self, event: str, **fields: Any) -> None:
        if self._logger is None:
            return
        if self.mode == "accepted" and event == "asr" and not fields.get("accepted"):
            return
        self._sequence += 1
        payload = {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            "seq": self._sequence,
            "event": event,
            **fields,
        }
        try:
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            self._logger.info(line)
            self._warned = False
        except Exception as exc:
            if not self._warned:
                print(f"[ASR-LOG] Ошибка записи {self.path}: {exc}")
                self._warned = True

    def close(self) -> None:
        if self._logger is None:
            return
        for handler in tuple(self._logger.handlers):
            handler.close()
            self._logger.removeHandler(handler)
        self._logger = None
