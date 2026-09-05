from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BEAM_COMMANDS = "0123456789AB"
DEFAULT_SERIAL_DEVICE = "/dev/ttyACM0"


@dataclass(frozen=True)
class ChannelStats:
    channel: int
    rms_dbfs: float
    peak_dbfs: float
    clipping_percent: float


@dataclass(frozen=True)
class SegmentStats:
    channel: int
    noise_rms_dbfs: float
    speech_rms_dbfs: float
    snr_db: float


@dataclass(frozen=True)
class RecordingSummary:
    path: Path
    position_deg: float | None
    beam: str | None
    role: str
    snr_db: tuple[float, ...]


def _dbfs(value: float) -> float:
    return -120.0 if value <= 0 else 20.0 * math.log10(value)


def analyze_samples(samples: np.ndarray, gain: float = 1.0) -> list[ChannelStats]:
    if samples.ndim != 2:
        raise ValueError("samples must have shape (frames, channels)")
    normalized = samples.astype(np.float64) / 32768.0
    normalized *= gain
    result: list[ChannelStats] = []
    for index in range(normalized.shape[1]):
        channel = normalized[:, index]
        clipping = float(np.mean(np.abs(channel) >= (32760.0 / 32768.0)) * 100.0)
        channel = np.clip(channel, -1.0, 1.0)
        rms = float(np.sqrt(np.mean(np.square(channel)))) if channel.size else 0.0
        peak = float(np.max(np.abs(channel))) if channel.size else 0.0
        result.append(ChannelStats(index, _dbfs(rms), _dbfs(peak), clipping))
    return result


def analyze_noise_and_speech(
    samples: np.ndarray,
    sample_rate: int,
    noise_seconds: float,
) -> list[SegmentStats]:
    """Compare the initial noise reference with the remaining recording.

    ``snr_db`` is intentionally a rough ratio between the RMS of the speech
    section (which still contains background noise) and the RMS of the noise
    section. It is useful for comparing microphones and beam settings, but is
    not an estimate of clean-speech SNR.
    """

    if samples.ndim != 2:
        raise ValueError("samples must have shape (frames, channels)")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than zero")
    if noise_seconds <= 0:
        raise ValueError("noise_seconds must be greater than zero")

    split = round(noise_seconds * sample_rate)
    if split <= 0 or split >= len(samples):
        raise ValueError("noise_seconds must be shorter than the recording")

    normalized = samples.astype(np.float64) / 32768.0
    noise = normalized[:split]
    speech = normalized[split:]
    result: list[SegmentStats] = []
    for index in range(normalized.shape[1]):
        noise_rms = float(np.sqrt(np.mean(np.square(noise[:, index]))))
        speech_rms = float(np.sqrt(np.mean(np.square(speech[:, index]))))
        if noise_rms == 0.0:
            snr = math.inf if speech_rms > 0.0 else 0.0
        elif speech_rms == 0.0:
            snr = -math.inf
        else:
            snr = 20.0 * math.log10(speech_rms / noise_rms)
        result.append(
            SegmentStats(
                channel=index,
                noise_rms_dbfs=_dbfs(noise_rms),
                speech_rms_dbfs=_dbfs(speech_rms),
                snr_db=snr,
            )
        )
    return result


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("Only 16-bit PCM WAV files are supported")
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        raw = source.readframes(source.getnframes())
    samples = np.frombuffer(raw, dtype="<i2")
    if samples.size % channels:
        raise ValueError("Invalid interleaved WAV data")
    return sample_rate, samples.reshape((-1, channels))


def split_channels(path: Path, output_dir: Path | None = None) -> list[Path]:
    """Write every interleaved input channel as a separate mono WAV."""

    sample_rate, samples = read_wav(path)
    target = output_dir or path.parent / f"{path.stem}-channels"
    target.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for channel in range(samples.shape[1]):
        output = target / f"{path.stem}-CH{channel}.wav"
        with wave.open(str(output), "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(sample_rate)
            destination.writeframes(samples[:, channel].astype("<i2", copy=False).tobytes())
        outputs.append(output)
    return outputs


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("значение не должно быть отрицательным")
    return parsed


def beam_command(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 1 or normalized not in BEAM_COMMANDS:
        raise argparse.ArgumentTypeError("beam должен быть одним из 0..9, A, B")
    return normalized


def build_record_command(device: str, seconds: int, output: Path) -> list[str]:
    return [
        "arecord", "-q", "-D", device, "-t", "wav", "-f", "S16_LE",
        "-r", "48000", "-c", "8", "-d", str(seconds), str(output),
    ]


def metadata_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".json")


def send_fixed_beam(serial_device: str, beam: str) -> None:
    normalized = beam.strip().upper()
    if len(normalized) != 1 or normalized not in BEAM_COMMANDS:
        raise ValueError("beam должен быть одним из 0..9, A, B")
    flags = os.O_RDWR | getattr(os, "O_NOCTTY", 0)
    descriptor = os.open(serial_device, flags)
    try:
        written = os.write(descriptor, normalized.encode("ascii"))
        if written != 1:
            raise OSError(f"не удалось отправить beam {normalized}")
    finally:
        os.close(descriptor)


def write_metadata(
    output: Path,
    *,
    timestamp: str,
    device: str,
    duration: int,
    gain: float,
    beam: str | None,
    position_deg: float | None,
    distance_m: float | None,
    noise_seconds: float,
    beam_settle_seconds: float,
    extra: dict[str, object] | None = None,
) -> Path:
    sidecar = metadata_path(output)
    payload = {
        "timestamp": timestamp,
        "device": device,
        "duration": duration,
        "gain": gain,
        "beam": beam,
        "position_deg": position_deg,
        "distance_m": distance_m,
        "noise_seconds": noise_seconds,
        "beam_settle_seconds": beam_settle_seconds,
    }
    if extra:
        payload.update(extra)
    sidecar.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return sidecar


def read_metadata(path: Path) -> dict[str, object]:
    sidecar = metadata_path(path)
    if not sidecar.exists():
        return {}
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Некорректный sidecar: {sidecar}")
    return payload


def record(
    device: str,
    seconds: int,
    output: Path,
    *,
    gain: float = 16.0,
    beam: str | None = None,
    serial_device: str = DEFAULT_SERIAL_DEVICE,
    position_deg: float | None = None,
    distance_m: float | None = None,
    noise_seconds: float = 0.0,
    beam_settle_seconds: float = 0.3,
) -> None:
    if noise_seconds < 0 or noise_seconds >= seconds:
        raise ValueError("noise_seconds должен быть меньше длительности записи")
    if beam_settle_seconds < 0:
        raise ValueError("beam_settle_seconds не должен быть отрицательным")
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized_beam = beam.strip().upper() if beam is not None else None
    applied_beam_settle_seconds = 0.0
    if normalized_beam is not None:
        print(f"Beam {normalized_beam}: {serial_device}")
        send_fixed_beam(serial_device, normalized_beam)
        applied_beam_settle_seconds = beam_settle_seconds
        if beam_settle_seconds > 0:
            print(f"Стабилизация beam: {beam_settle_seconds:g} c")
            time.sleep(beam_settle_seconds)
    command = build_record_command(device, seconds, output)
    print(f"Запись {seconds:g} с: {output}")
    timestamp = datetime.now(timezone.utc).isoformat()
    subprocess.run(command, check=True)
    sidecar = write_metadata(
        output,
        timestamp=timestamp,
        device=device,
        duration=seconds,
        gain=gain,
        beam=normalized_beam,
        position_deg=position_deg,
        distance_m=distance_m,
        noise_seconds=noise_seconds,
        beam_settle_seconds=applied_beam_settle_seconds,
    )
    print(f"Метаданные: {sidecar}")


def _print_stats(samples: np.ndarray, gain: float) -> None:
    print("channel  rms_dbfs  peak_dbfs  clipping")
    for item in analyze_samples(samples, gain):
        marker = "  <- beamformed" if item.channel == 6 else ""
        print(
            f"CH{item.channel:<2} {item.rms_dbfs:9.2f} {item.peak_dbfs:10.2f} "
            f"{item.clipping_percent:8.4f}%{marker}"
        )


def _format_snr(value: float) -> str:
    if value == math.inf:
        return "+inf"
    if value == -math.inf:
        return "-inf"
    if not math.isfinite(value):
        return "n/a"
    return f"{value:.2f}"


def _print_segment_stats(
    samples: np.ndarray,
    sample_rate: int,
    noise_seconds: float,
) -> None:
    duration = len(samples) / sample_rate
    print(
        f"\nШум 0.00–{noise_seconds:.2f} s / "
        f"речь {noise_seconds:.2f}–{duration:.2f} s (raw, gain x1):"
    )
    print("channel  noise_rms  speech_rms  rough_snr")
    for item in analyze_noise_and_speech(samples, sample_rate, noise_seconds):
        marker = "  <- beamformed" if item.channel == 6 else ""
        print(
            f"CH{item.channel:<2} {item.noise_rms_dbfs:10.2f} "
            f"{item.speech_rms_dbfs:11.2f} {_format_snr(item.snr_db):>10} dB{marker}"
        )


def print_report(
    path: Path,
    gain: float = 16.0,
    noise_seconds: float | None = None,
) -> None:
    sample_rate, samples = read_wav(path)
    duration = len(samples) / sample_rate
    metadata = read_metadata(path)
    if noise_seconds is None:
        stored_noise_seconds = metadata.get("noise_seconds", 0.0)
        if not isinstance(stored_noise_seconds, (int, float)):
            raise ValueError("noise_seconds в sidecar должен быть числом")
        noise_seconds = float(stored_noise_seconds)
    print(f"Файл: {path}")
    print(f"Формат: {sample_rate} Hz, {samples.shape[1]} ch, {duration:.2f} s")
    if metadata:
        print(f"Метаданные: {metadata_path(path)}")
    print("\nСырые данные (gain x1):")
    _print_stats(samples, 1.0)
    if noise_seconds:
        _print_segment_stats(samples, sample_rate, noise_seconds)
    if gain != 1.0:
        print(f"\nПосле программного gain x{gain:g} и ограничения сигнала:")
        _print_stats(samples, gain)


def beam_for_position(
    position_deg: int,
    *,
    offset: int = 0,
    clockwise: bool = True,
) -> str:
    if position_deg % 30:
        raise ValueError("position_deg должен быть кратен 30")
    sector = (position_deg // 30) % 12
    if not clockwise:
        sector = -sector
    return BEAM_COMMANDS[(sector + offset) % 12]


def _opposite_beam(beam: str) -> str:
    return BEAM_COMMANDS[(BEAM_COMMANDS.index(beam) + 6) % 12]


def record_circle(
    output_dir: Path,
    *,
    device: str,
    serial_device: str,
    seconds: int,
    gain: float,
    distance_m: float,
    noise_seconds: float,
    beam_settle_seconds: float = 0.3,
    beam_offset: int = 0,
    clockwise: bool = True,
    opposite: bool = False,
    wait_for_position: Callable[[str], str] = input,
) -> list[Path]:
    """Interactively capture 12 positions with aligned beam settings."""

    if noise_seconds <= 0 or noise_seconds >= seconds:
        raise ValueError("для circle noise_seconds должен быть между 0 и duration")
    output_dir.mkdir(parents=True, exist_ok=True)
    recordings: list[Path] = []
    print(
        "Круговой тест: 12 позиций с шагом 30°. На каждой позиции первые "
        f"{noise_seconds:g} с сохраняйте тишину, затем произнесите тестовую фразу."
    )
    for position_deg in range(0, 360, 30):
        aligned = beam_for_position(
            position_deg,
            offset=beam_offset,
            clockwise=clockwise,
        )
        beams = [("aligned", aligned)]
        if opposite:
            beams.append(("opposite", _opposite_beam(aligned)))
        for label, beam in beams:
            wait_for_position(
                f"\nПозиция {position_deg:03d}°, {distance_m:g} м, "
                f"beam {beam} ({label}). Нажмите Enter, затем "
                f"молчите {noise_seconds:g} с..."
            )
            output = output_dir / f"pos-{position_deg:03d}-{label}-beam-{beam}.wav"
            record(
                device,
                seconds,
                output,
                gain=gain,
                beam=beam,
                serial_device=serial_device,
                position_deg=float(position_deg),
                distance_m=distance_m,
                noise_seconds=noise_seconds,
                beam_settle_seconds=beam_settle_seconds,
            )
            recordings.append(output)
    print(f"\nКруговой тест завершён: {len(recordings)} записей в {output_dir}")
    print(f"Сводка: .venv/bin/kot-mic-calibrate summarize {output_dir}")
    return recordings


def _recording_role(path: Path) -> str:
    if "-aligned-" in path.stem:
        return "aligned"
    if "-opposite-" in path.stem:
        return "opposite"
    return "manual"


def summarize_directory(
    directory: Path,
    *,
    noise_seconds: float | None = None,
) -> list[RecordingSummary]:
    """Collect comparable raw per-channel SNR values for a circle run."""

    paths = sorted(directory.glob("*.wav"))
    if not paths:
        raise ValueError(f"В {directory} нет WAV-файлов")

    rows: list[RecordingSummary] = []
    for path in paths:
        metadata = read_metadata(path)
        recording_noise = noise_seconds
        if recording_noise is None:
            stored = metadata.get("noise_seconds")
            if not isinstance(stored, (int, float)) or stored <= 0:
                raise ValueError(
                    f"Для {path.name} не задан noise_seconds: "
                    "добавьте --noise-seconds"
                )
            recording_noise = float(stored)

        sample_rate, samples = read_wav(path)
        stats = analyze_noise_and_speech(samples, sample_rate, recording_noise)
        position = metadata.get("position_deg")
        beam = metadata.get("beam")
        rows.append(
            RecordingSummary(
                path=path,
                position_deg=float(position) if isinstance(position, (int, float)) else None,
                beam=beam if isinstance(beam, str) else None,
                role=_recording_role(path),
                snr_db=tuple(item.snr_db for item in stats),
            )
        )
    return rows


def print_directory_summary(
    directory: Path,
    *,
    noise_seconds: float | None = None,
) -> None:
    rows = summarize_directory(directory, noise_seconds=noise_seconds)
    channel_count = max(len(row.snr_db) for row in rows)
    channel_headers = " ".join(f"CH{channel}" for channel in range(channel_count))
    print(f"Каталог: {directory}")
    print("rough SNR = RMS участка речи / RMS первых секунд шума, raw gain x1")
    print(f"position role     beam best {channel_headers}")
    for row in rows:
        position = "  -" if row.position_deg is None else f"{row.position_deg:3.0f}"
        best = max(range(len(row.snr_db)), key=row.snr_db.__getitem__)
        values = " ".join(f"{_format_snr(value):>5}" for value in row.snr_db)
        print(
            f"{position:>8} {row.role:<8} {(row.beam or '-'):>4} "
            f"CH{best:<2} {values}  {row.path.name}"
        )

    by_position: dict[float, dict[str, RecordingSummary]] = {}
    for row in rows:
        if row.position_deg is not None and row.role in {"aligned", "opposite"}:
            by_position.setdefault(row.position_deg, {})[row.role] = row
    comparisons = [
        (position, pair["aligned"], pair["opposite"])
        for position, pair in sorted(by_position.items())
        if "aligned" in pair and "opposite" in pair
    ]
    if comparisons and channel_count > 6:
        print("\nПроверка beamforming CH6 (положительная delta ожидаема):")
        print("position aligned opposite delta")
        for position, aligned, opposite in comparisons:
            aligned_snr = aligned.snr_db[6]
            opposite_snr = opposite.snr_db[6]
            print(
                f"{position:7.0f}° {_format_snr(aligned_snr):>7} "
                f"{_format_snr(opposite_snr):>8} "
                f"{_format_snr(aligned_snr - opposite_snr):>6} dB"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Запись и анализ каналов MA-USB8")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record_parser = subparsers.add_parser("record", help="записать 8-канальный WAV")
    record_parser.add_argument("output", type=Path)
    record_parser.add_argument("--device", default="hw:MicArray,0")
    record_parser.add_argument("--seconds", type=positive_int, default=15)
    record_parser.add_argument("--gain", type=float, default=16.0)
    record_parser.add_argument("--beam", type=beam_command)
    record_parser.add_argument("--serial-device", default=DEFAULT_SERIAL_DEVICE)
    record_parser.add_argument("--position-deg", type=float)
    record_parser.add_argument("--distance-m", type=positive_float)
    record_parser.add_argument("--noise-seconds", type=nonnegative_float, default=0.0)
    record_parser.add_argument(
        "--beam-settle-seconds",
        type=nonnegative_float,
        default=0.3,
        help="пауза после смены beam перед arecord",
    )
    analyze_parser = subparsers.add_parser("analyze", help="показать уровни каналов WAV")
    analyze_parser.add_argument("input", type=Path)
    analyze_parser.add_argument("--gain", type=float, default=16.0)
    analyze_parser.add_argument("--noise-seconds", type=nonnegative_float)
    split_parser = subparsers.add_parser(
        "split",
        help="разложить 8-канальный WAV на отдельные mono-файлы",
    )
    split_parser.add_argument("input", type=Path)
    split_parser.add_argument("output_dir", type=Path, nargs="?")
    circle_parser = subparsers.add_parser(
        "circle",
        help="интерактивно записать 12 направлений с фиксированным beam",
    )
    circle_parser.add_argument("output_dir", type=Path)
    circle_parser.add_argument("--device", default="hw:MicArray,0")
    circle_parser.add_argument("--serial-device", default=DEFAULT_SERIAL_DEVICE)
    circle_parser.add_argument("--seconds", type=positive_int, default=15)
    circle_parser.add_argument("--gain", type=float, default=16.0)
    circle_parser.add_argument("--distance-m", type=positive_float, default=1.0)
    circle_parser.add_argument("--noise-seconds", type=positive_float, default=3.0)
    circle_parser.add_argument(
        "--beam-settle-seconds",
        type=nonnegative_float,
        default=0.3,
        help="пауза после смены beam перед каждой записью",
    )
    circle_parser.add_argument("--beam-offset", type=int, default=0)
    circle_parser.add_argument(
        "--counterclockwise",
        action="store_true",
        help="нумерация beam идёт против часовой стрелки",
    )
    circle_parser.add_argument(
        "--opposite",
        action="store_true",
        help="дополнительно записать луч, повёрнутый на 180°",
    )
    summary_parser = subparsers.add_parser(
        "summarize",
        help="сравнить rough SNR всех каналов в каталоге записей",
    )
    summary_parser.add_argument("input_dir", type=Path)
    summary_parser.add_argument("--noise-seconds", type=positive_float)

    sweep_parser = subparsers.add_parser(
        "sweep",
        help="автоматически проверить все beam с неподвижной колонкой",
    )
    sweep_parser.add_argument("output_dir", type=Path)
    sweep_parser.add_argument("--device", default="hw:MicArray,0")
    sweep_parser.add_argument("--serial-device", default=DEFAULT_SERIAL_DEVICE)
    sweep_parser.add_argument("--position-deg", type=float, default=0.0)
    sweep_parser.add_argument("--label", default="front")
    sweep_parser.add_argument("--distance-m", type=positive_float, default=1.0)
    sweep_parser.add_argument("--repeats", type=positive_int, default=1)
    sweep_parser.add_argument("--gain", type=float, default=16.0)
    sweep_parser.add_argument("--pre-roll-seconds", type=positive_float, default=1.0)
    sweep_parser.add_argument("--post-roll-seconds", type=positive_float, default=1.0)
    sweep_parser.add_argument("--signal-seconds", type=positive_float, default=3.0)
    sweep_parser.add_argument("--signal-rms-dbfs", type=float, default=-18.0)
    sweep_parser.add_argument("--signal-file", type=Path)
    sweep_parser.add_argument(
        "--player",
        help="команда playback; {file} заменяется путём к тестовому WAV",
    )
    sweep_parser.add_argument(
        "--beams",
        default=BEAM_COMMANDS,
        help="например 0123456789AB или 0,3,6,9",
    )
    sweep_parser.add_argument(
        "--beam-settle-seconds",
        type=nonnegative_float,
        default=0.5,
    )
    sweep_parser.add_argument(
        "--start-allowance-seconds",
        type=nonnegative_float,
        default=2.0,
        help="запас записи и диапазон поиска задержки запуска player",
    )
    sweep_parser.add_argument(
        "--preview",
        action="store_true",
        help="один раз проиграть сигнал для настройки громкости",
    )
    sweep_parser.add_argument(
        "--skip-prompt",
        action="store_true",
        help="начать без ожидания Enter",
    )

    sweep_summary_parser = subparsers.add_parser(
        "sweep-summary",
        help="ранжировать CH6 по результатам fixed-source sweep",
    )
    sweep_summary_parser.add_argument("input_dir", type=Path)
    sweep_summary_parser.add_argument(
        "--no-write",
        action="store_true",
        help="не создавать CSV/JSON",
    )

    fit_map_parser = subparsers.add_parser(
        "fit-map",
        help="восстановить offset/CW по sweep из нескольких положений",
    )
    fit_map_parser.add_argument("input_dirs", type=Path, nargs="+")
    fit_map_parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "record":
            record(
                args.device,
                args.seconds,
                args.output,
                gain=args.gain,
                beam=args.beam,
                serial_device=args.serial_device,
                position_deg=args.position_deg,
                distance_m=args.distance_m,
                noise_seconds=args.noise_seconds,
                beam_settle_seconds=args.beam_settle_seconds,
            )
            print_report(args.output, args.gain, args.noise_seconds)
        elif args.command == "analyze":
            print_report(args.input, args.gain, args.noise_seconds)
        elif args.command == "split":
            outputs = split_channels(args.input, args.output_dir)
            print(f"Создано {len(outputs)} mono-файлов в {outputs[0].parent}")
        elif args.command == "circle":
            record_circle(
                args.output_dir,
                device=args.device,
                serial_device=args.serial_device,
                seconds=args.seconds,
                gain=args.gain,
                distance_m=args.distance_m,
                noise_seconds=args.noise_seconds,
                beam_settle_seconds=args.beam_settle_seconds,
                beam_offset=args.beam_offset,
                clockwise=not args.counterclockwise,
                opposite=args.opposite,
            )
        elif args.command == "summarize":
            print_directory_summary(
                args.input_dir,
                noise_seconds=args.noise_seconds,
            )
        elif args.command == "sweep":
            from .beam_sweep import record_fixed_source_sweep

            record_fixed_source_sweep(
                args.output_dir,
                device=args.device,
                serial_device=args.serial_device,
                position_deg=args.position_deg,
                source_label=args.label,
                distance_m=args.distance_m,
                repeats=args.repeats,
                pre_roll_seconds=args.pre_roll_seconds,
                post_roll_seconds=args.post_roll_seconds,
                signal_seconds=args.signal_seconds,
                signal_rms_dbfs=args.signal_rms_dbfs,
                beam_settle_seconds=args.beam_settle_seconds,
                start_allowance_seconds=args.start_allowance_seconds,
                gain=args.gain,
                player=args.player,
                signal_file=args.signal_file,
                beams=args.beams,
                preview=args.preview,
                skip_prompt=args.skip_prompt,
            )
        elif args.command == "sweep-summary":
            from .beam_sweep import print_sweep_report, summarize_fixed_source_sweep

            report = summarize_fixed_source_sweep(args.input_dir)
            print_sweep_report(report, write_files=not args.no_write)
        elif args.command == "fit-map":
            from .beam_sweep import fit_beam_mapping, print_beam_mapping

            result = fit_beam_mapping(args.input_dirs)
            print_beam_mapping(result, output=args.output)
        else:  # pragma: no cover - argparse enforces known subcommands
            raise ValueError(f"Неизвестная команда: {args.command}")
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Ошибка: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
