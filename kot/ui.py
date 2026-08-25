from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["idle", "music", "listening", "thinking", "speaking", "offline", "error"]


@dataclass(frozen=True)
class ModeUi:
    face: str
    title: str
    icon: str
    message: str


# This is the single source of truth for all mode-dependent UI content.
# The browser does not contain its own copies of faces, titles, icons or messages.
MODE_UI: dict[Mode, ModeUi] = {
    "idle": ModeUi(
        face=" /\\_/\\\n( -.- )\n > ^ <",
        title="IDLE",
        icon="·",
        message="Жду общения",
    ),
    "music": ModeUi(
        face=" /\\_/\\   ♪\n( ^.^ )\n > ^ <",
        title="MUSIC",
        icon="♪",
        message="Музыка играет",
    ),
    "listening": ModeUi(
        face=" /\\_/\\\n( O.O )\n > ^ <",
        title="LISTEN",
        icon="◉",
        message="Слушаю",
    ),
    "thinking": ModeUi(
        face=" /\\_/\\\n( -_o )\n > ? <",
        title="THINK",
        icon="?",
        message="Думаю",
    ),
    "speaking": ModeUi(
        face=" /\\_/\\\n( ^O^ )\n > ^ <",
        title="SPEAK",
        icon=">",
        message="Отвечаю",
    ),
    "offline": ModeUi(
        face=" /\\_/\\\n( x.x )\n > _ <",
        title="OFFLINE",
        icon="×",
        message="Мозги Кота недоступны",
    ),
    "error": ModeUi(
        face=" /\\_/\\\n( >.< )\n > ! <",
        title="ERROR",
        icon="!",
        message="Ошибка",
    ),
}

ACK_TEXT = "Мяу, щас!"

# Voice-worker details shown in the same message field for failures.
MIC_UNAVAILABLE_TEXT = "Микрофон недоступен"
AUDIO_UNAVAILABLE_TEXT = "Аудиопоток недоступен"
MIC_STREAM_STOPPED_TEXT = "Поток микрофона остановлен"
