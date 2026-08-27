from __future__ import annotations

import re


WAKE_PHRASES = (
    "эй кот",
    "эй код",
    "ей кот",
    "ей код",
    "эйкот",
    "эйкод",
    "ейкот",
    "ейкод",
)

COMMAND_WAKE_PREFIXES = (
    "эйкот",
    "эйкод",
    "ейкот",
    "ейкод",
)


def normalize_text(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def find_wake(text: str) -> tuple[bool, str, str]:
    normalized = normalize_text(text)
    best_match: re.Match[str] | None = None

    for phrase in WAKE_PHRASES:
        # Word boundaries are essential here: a plain substring search makes
        # "бэй кот" match "эй кот" and causes false wake-ups from music.
        match = re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized)
        if match is not None and (best_match is None or match.start() < best_match.start()):
            best_match = match

    if best_match is None:
        return False, normalized, ""

    return True, normalized, normalized[best_match.end() :].strip()


def strip_wake_from_command(text: str) -> str:
    found, normalized, command = find_wake(text)
    if found:
        return command

    for prefix in COMMAND_WAKE_PREFIXES:
        if normalized == prefix:
            return ""
        if normalized.startswith(f"{prefix} "):
            return normalized[len(prefix) :].strip()
    return normalized


def is_probable_wake_only(text: str) -> bool:
    """Reject a short distorted wake phrase left in the command pre-roll."""
    words = normalize_text(text).split()
    return 1 <= len(words) <= 3 and words[-1] in {"кот", "код"}
