from __future__ import annotations

import argparse
import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class ChannelStats:
    channel: int
    rms_dbfs: float
    peak_dbfs: float
    clipping_percent: float


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


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return parsed


def build_record_command(device: str, seconds: int, output: Path) -> list[str]:
    return [
        "arecord", "-q", "-D", device, "-t", "wav", "-f", "S16_LE",
        "-r", "48000", "-c", "8", "-d", str(seconds), str(output),
    ]


def record(device: str, seconds: int, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = build_record_command(device, seconds, output)
    print(f"Запись {seconds:g} с: {output}")
    subprocess.run(command, check=True)


def _print_stats(samples: np.ndarray, gain: float) -> None:
    print("channel  rms_dbfs  peak_dbfs  clipping")
    for item in analyze_samples(samples, gain):
        marker = "  <- beamformed" if item.channel == 6 else ""
        print(
            f"CH{item.channel:<2} {item.rms_dbfs:9.2f} {item.peak_dbfs:10.2f} "
            f"{item.clipping_percent:8.4f}%{marker}"
        )


def print_report(path: Path, gain: float = 16.0) -> None:
    sample_rate, samples = read_wav(path)
    duration = len(samples) / sample_rate
    print(f"Файл: {path}")
    print(f"Формат: {sample_rate} Hz, {samples.shape[1]} ch, {duration:.2f} s")
    print("\nСырые данные (gain x1):")
    _print_stats(samples, 1.0)
    if gain != 1.0:
        print(f"\nПосле программного gain x{gain:g} и ограничения сигнала:")
        _print_stats(samples, gain)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Запись и анализ каналов MA-USB8")
    subparsers = parser.add_subparsers(dest="command", required=True)
    record_parser = subparsers.add_parser("record", help="записать 8-канальный WAV")
    record_parser.add_argument("output", type=Path)
    record_parser.add_argument("--device", default="hw:MicArray,0")
    record_parser.add_argument("--seconds", type=positive_int, default=15)
    record_parser.add_argument("--gain", type=float, default=16.0)
    analyze_parser = subparsers.add_parser("analyze", help="показать уровни каналов WAV")
    analyze_parser.add_argument("input", type=Path)
    analyze_parser.add_argument("--gain", type=float, default=16.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "record":
            record(args.device, args.seconds, args.output)
            print_report(args.output, args.gain)
        else:
            print_report(args.input, args.gain)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Ошибка: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
