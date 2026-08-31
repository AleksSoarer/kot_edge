from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

SYSTEM_LOG_PATH = Path("/var/log/kot-edge/recognition.jsonl")


def default_log_path() -> Path:
    configured = os.getenv("KOT_RECOGNITION_LOG", "").strip()
    if configured:
        return Path(configured).expanduser()
    if SYSTEM_LOG_PATH.exists():
        return SYSTEM_LOG_PATH
    return Path.home() / "kot-logs" / "recognition.jsonl"


DEFAULT_LOG_PATH = default_log_path()
DEFAULT_TOP = 10


def rotated_log_paths(path: Path) -> list[Path]:
    """Return RotatingFileHandler files from oldest to newest."""

    rotations: list[tuple[int, Path]] = []
    prefix = f"{path.name}."
    if path.parent.exists():
        for candidate in path.parent.glob(f"{path.name}.*"):
            suffix = candidate.name[len(prefix) :]
            if candidate.is_file() and suffix.isdigit():
                rotations.append((int(suffix), candidate))

    # RotatingFileHandler uses .1 for the newest backup, so the largest
    # suffix is the oldest file still available.
    files = [candidate for _, candidate in sorted(rotations, reverse=True)]
    if path.is_file():
        files.append(path)
    return files


def load_records(
    path: Path,
    *,
    warn: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[Path], int]:
    """Load the current JSONL log and its numeric rotations chronologically."""

    files = rotated_log_paths(path)
    if not files:
        raise FileNotFoundError(path)

    records: list[dict[str, Any]] = []
    invalid_lines = 0
    readable_files = 0
    for source in files:
        try:
            lines: Iterable[str]
            with source.open(encoding="utf-8") as opened:
                lines = opened
                for line_number, line in enumerate(lines, start=1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise ValueError("ожидался JSON-объект")
                    except (json.JSONDecodeError, ValueError) as exc:
                        invalid_lines += 1
                        if warn is not None:
                            warn(f"{source}:{line_number}: строка пропущена: {exc}")
                        continue
                    records.append(record)
            readable_files += 1
        except OSError as exc:
            invalid_lines += 1
            if warn is not None:
                warn(f"не удалось прочитать {source}: {exc}")

    if readable_files == 0:
        raise OSError(f"ни один файл журнала не удалось прочитать: {path}")

    return records, files, invalid_lines


def _text(record: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _top(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [{"text": text, "count": count} for text, count in ranked[:limit]]


def build_report(
    path: Path,
    *,
    top: int = DEFAULT_TOP,
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if top < 1:
        raise ValueError("top must be positive")

    records, files, invalid_lines = load_records(path, warn=warn)
    session_ids: set[str] = set()
    event_counts: Counter[str] = Counter()
    accepted_wake_phrases: Counter[str] = Counter()
    rejected_wake_candidates: Counter[str] = Counter()
    command_phrases: Counter[str] = Counter()
    beam_sectors: Counter[int] = Counter()
    capture_routes: Counter[tuple[int, int]] = Counter()
    last_beam_diagnostic: dict[str, Any] | None = None
    accepted_wakes = 0
    rejected_wakes = 0
    commands = 0
    timeouts = 0

    first_timestamp: str | None = None
    last_timestamp: str | None = None

    for record in records:
        session_id = record.get("session_id")
        if isinstance(session_id, str) and session_id:
            session_ids.add(session_id)

        event = record.get("event")
        if isinstance(event, str) and event:
            event_counts[event] += 1

        timestamp = record.get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp

        if event == "wake_detected":
            accepted_wakes += 1
            phrase = _text(record, "source_text", "normalized", "text")
            if phrase:
                accepted_wake_phrases[phrase] += 1

        if (
            event == "asr"
            and record.get("stage") == "wake"
            and record.get("decision") == "rejected"
        ):
            rejected_wakes += 1
            phrase = _text(record, "normalized", "text")
            if phrase:
                rejected_wake_candidates[phrase] += 1

        if event == "command":
            commands += 1
            phrase = _text(record, "text", "source_text", "normalized")
            if phrase:
                command_phrases[phrase] += 1

        if event == "timeout":
            timeouts += 1

        if event == "session_start":
            selected_channel = record.get("selected_channel")
            wake_channel = record.get("wake_channel")
            if isinstance(selected_channel, int) and isinstance(wake_channel, int):
                capture_routes[(wake_channel, selected_channel)] += 1

        if event == "beam_diagnostic":
            last_beam_diagnostic = {
                field: record.get(field)
                for field in (
                    "connected",
                    "locked",
                    "frames_total",
                    "frames_since_open",
                    "last_frame_age",
                    "last_contrast",
                    "candidate_sector",
                    "voted_sector",
                    "commanded_sector",
                )
            }

        # ASR wake observations carry the sector that was active while the
        # audio was captured. NPU wake events do not have a matching ASR row.
        is_beam_observation = event == "asr" and record.get("stage") == "wake"
        is_npu_observation = event == "wake_detected" and record.get("backend") == "npu"
        sector = record.get("beam_sector")
        if (is_beam_observation or is_npu_observation) and isinstance(sector, int):
            beam_sectors[sector] += 1

    return {
        "log_path": str(path),
        "files": [str(file) for file in files],
        "sessions": len(session_ids),
        "events": len(records),
        "invalid_lines": invalid_lines,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "event_counts": dict(sorted(event_counts.items())),
        "accepted_wakes": accepted_wakes,
        "accepted_wake_phrases": _top(accepted_wake_phrases, top),
        "rejected_wakes": rejected_wakes,
        "rejected_wake_candidates": _top(rejected_wake_candidates, top),
        "commands": commands,
        "command_phrases": _top(command_phrases, top),
        "timeouts": timeouts,
        "beam_sectors": [
            {"sector": sector, "count": beam_sectors[sector]}
            for sector in sorted(beam_sectors)
        ],
        "capture_routes": [
            {
                "wake_channel": wake_channel,
                "command_channel": command_channel,
                "sessions": capture_routes[(wake_channel, command_channel)],
            }
            for wake_channel, command_channel in sorted(capture_routes)
        ],
        "last_beam_diagnostic": last_beam_diagnostic,
    }


def _print_ranked(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"{title}:")
    if not rows:
        print("  —")
        return
    for row in rows:
        print(f"  {row['count']:>5}  {row['text']}")


def print_report(report: dict[str, Any]) -> None:
    print(f"Журнал:              {report['log_path']}")
    print(f"Файлов:              {len(report['files'])}")
    print(f"Сессий:              {report['sessions']}")
    print(f"Событий:             {report['events']}")
    print(f"Битых строк:         {report['invalid_lines']}")
    print(f"Wake-word принят:    {report['accepted_wakes']}")
    print(f"Wake отклонён:       {report['rejected_wakes']}")
    print(f"Команд:              {report['commands']}")
    print(f"Timeout:             {report['timeouts']}")
    if report["first_timestamp"]:
        print(f"Период:              {report['first_timestamp']} — {report['last_timestamp']}")

    print()
    event_counts = ", ".join(
        f"{event}={count}" for event, count in report["event_counts"].items()
    )
    print(f"События по типам:    {event_counts or '—'}")
    print()
    _print_ranked("Принятые wake-word", report["accepted_wake_phrases"])
    print()
    _print_ranked("Отклонённые кандидаты wake-word", report["rejected_wake_candidates"])
    print()
    _print_ranked("Команды", report["command_phrases"])
    print()
    print("Beam-секторы:")
    if not report["beam_sectors"]:
        print("  —")
    else:
        print(
            "  "
            + "  ".join(
                f"{row['sector']}={row['count']}" for row in report["beam_sectors"]
            )
        )
    print()
    print("Тракты wake → command:")
    if not report["capture_routes"]:
        print("  —")
    else:
        for route in report["capture_routes"]:
            print(
                f"  CH{route['wake_channel']} → CH{route['command_channel']}: "
                f"{route['sessions']} сессий"
            )

    diagnostics = report["last_beam_diagnostic"]
    print()
    if diagnostics is None:
        print("Последняя hotmap-диагностика: —")
    else:
        print(
            "Последняя hotmap-диагностика: "
            f"connected={diagnostics['connected']} "
            f"frames={diagnostics['frames_total']} "
            f"age={diagnostics['last_frame_age']}s "
            f"contrast={diagnostics['last_contrast']} "
            f"sector={diagnostics['commanded_sector']}"
        )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидалось целое число") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Сводка JSONL-журнала распознавания Кота и его ротаций"
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help=f"текущий JSONL-файл (по умолчанию: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument(
        "--top",
        type=_positive_int,
        default=DEFAULT_TOP,
        help=f"число наиболее частых фраз (по умолчанию: {DEFAULT_TOP})",
    )
    parser.add_argument("--json", action="store_true", help="вывести машинный JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def warn(message: str) -> None:
        print(f"[ASR-REPORT] Предупреждение: {message}", file=sys.stderr)

    try:
        report = build_report(args.path.expanduser(), top=args.top, warn=warn)
    except FileNotFoundError:
        print(f"Журнал не найден: {args.path.expanduser()}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Не удалось прочитать журнал: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
