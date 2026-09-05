from __future__ import annotations

import csv
import json
import math
import os
import shlex
import shutil
import subprocess
import time
import wave
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .calibrate import (
    BEAM_COMMANDS,
    build_record_command,
    metadata_path,
    read_metadata,
    read_wav,
    send_fixed_beam,
    write_metadata,
)

DEFAULT_SIGNAL_SAMPLE_RATE = 48_000
DEFAULT_SIGNAL_SECONDS = 3.0
DEFAULT_SIGNAL_RMS_DBFS = -18.0
DEFAULT_START_ALLOWANCE_SECONDS = 2.0
RAW_REFERENCE_CHANNELS = (0, 1, 2, 3, 4, 5, 7)
BEAMFORMED_CHANNEL = 6


@dataclass(frozen=True)
class SweepTake:
    path: Path
    beam: str
    repeat: int
    source_label: str
    position_deg: float | None
    detected_signal_start_seconds: float
    signal_duration_seconds: float
    ch6_noise_dbfs: float
    ch6_signal_dbfs: float
    ch6_snr_db: float
    ch6_peak_dbfs: float
    ch6_clipping_percent: float
    raw_reference_dbfs: float
    ch6_vs_raw_db: float


@dataclass(frozen=True)
class BeamAggregate:
    beam: str
    count: int
    ch6_signal_dbfs: float
    ch6_signal_std_db: float
    ch6_snr_db: float
    ch6_snr_std_db: float
    raw_reference_dbfs: float
    ch6_vs_raw_db: float
    ch6_vs_raw_std_db: float
    ch6_peak_dbfs: float
    ch6_clipping_percent: float


@dataclass(frozen=True)
class SweepReport:
    directory: Path
    takes: tuple[SweepTake, ...]
    beams: tuple[BeamAggregate, ...]
    source_label: str
    position_deg: float | None
    best_beam: str
    best_snr_beam: str
    opposite_beam: str | None
    score_span_db: float
    best_vs_opposite_db: float | None
    best_margin_db: float
    confidence: str


def _dbfs(value: float) -> float:
    return -120.0 if value <= 0 else 20.0 * math.log10(value)


def _rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def _mean(values: Iterable[float]) -> float:
    data = tuple(values)
    return float(np.mean(data)) if data else 0.0


def _std(values: Iterable[float]) -> float:
    data = tuple(values)
    return float(np.std(data)) if len(data) > 1 else 0.0


def _beam_sort_key(beam: str) -> int:
    return BEAM_COMMANDS.index(beam)


def opposite_beam(beam: str) -> str:
    return BEAM_COMMANDS[(BEAM_COMMANDS.index(beam) + 6) % len(BEAM_COMMANDS)]


def normalize_beams(values: Sequence[str] | str) -> tuple[str, ...]:
    if isinstance(values, str):
        compact = values.replace(",", " ").replace(";", " ")
        tokens = compact.split()
        if len(tokens) == 1 and len(tokens[0]) > 1:
            tokens = list(tokens[0])
    else:
        tokens = list(values)

    result: list[str] = []
    for value in tokens:
        beam = value.strip().upper()
        if len(beam) != 1 or beam not in BEAM_COMMANDS:
            raise ValueError("список beam должен содержать только 0..9, A, B")
        if beam not in result:
            result.append(beam)
    if not result:
        raise ValueError("список beam не должен быть пустым")
    return tuple(result)


def generate_calibration_signal(
    path: Path,
    *,
    duration_seconds: float = DEFAULT_SIGNAL_SECONDS,
    sample_rate: int = DEFAULT_SIGNAL_SAMPLE_RATE,
    rms_dbfs: float = DEFAULT_SIGNAL_RMS_DBFS,
    seed: int = 20_260_820,
) -> Path:
    """Create deterministic speech-band, pink-ish broadband noise.

    A broadband signal is deliberately used instead of a sine tone: a single
    frequency is too sensitive to room standing waves and can make one beam
    appear better only because of the room.
    """

    if duration_seconds <= 0:
        raise ValueError("duration_seconds должен быть больше нуля")
    if sample_rate <= 0:
        raise ValueError("sample_rate должен быть больше нуля")
    if not -60.0 <= rms_dbfs <= -6.0:
        raise ValueError("rms_dbfs должен быть между -60 и -6 dBFS")

    count = max(1, round(duration_seconds * sample_rate))
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(count)
    spectrum = np.fft.rfft(white)
    frequencies = np.fft.rfftfreq(count, 1.0 / sample_rate)

    # Smooth speech-band limits plus a gentle high-frequency roll-off.
    low_edge = np.clip((frequencies - 150.0) / 150.0, 0.0, 1.0)
    high_edge = np.clip((7_500.0 - frequencies) / 1_500.0, 0.0, 1.0)
    band = np.sin(low_edge * math.pi / 2.0) ** 2
    band *= np.sin(high_edge * math.pi / 2.0) ** 2
    pink_tilt = np.ones_like(frequencies)
    positive = frequencies > 0
    pink_tilt[positive] = np.sqrt(300.0 / np.maximum(frequencies[positive], 300.0))
    spectrum *= band * pink_tilt
    spectrum[0] = 0.0

    signal = np.fft.irfft(spectrum, n=count)
    signal -= float(np.mean(signal))

    # Slow amplitude modulation makes the acoustic signal easier to locate in
    # the recording while keeping its spectrum broad.
    timeline = np.arange(count, dtype=np.float64) / sample_rate
    envelope = 0.72 + 0.28 * np.square(np.sin(2.0 * math.pi * 2.3 * timeline))
    fade_samples = min(round(0.08 * sample_rate), count // 4)
    if fade_samples:
        fade = np.sin(np.linspace(0.0, math.pi / 2.0, fade_samples)) ** 2
        envelope[:fade_samples] *= fade
        envelope[-fade_samples:] *= fade[::-1]
    signal *= envelope

    target_rms = 10.0 ** (rms_dbfs / 20.0)
    current_rms = _rms(signal)
    if current_rms == 0.0:
        raise ValueError("не удалось сформировать тестовый сигнал")
    signal *= target_rms / current_rms

    # Keep digital headroom even when the selected RMS and random crest factor
    # would otherwise produce a near-full-scale peak.
    peak = float(np.max(np.abs(signal)))
    peak_limit = 10.0 ** (-3.0 / 20.0)
    if peak > peak_limit:
        signal *= peak_limit / peak

    pcm = np.rint(np.clip(signal, -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    return path


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        sample_rate = source.getframerate()
        if sample_rate <= 0:
            raise ValueError(f"Некорректный sample rate: {path}")
        return source.getnframes() / sample_rate


def resolve_player_command(player: str | None, signal_path: Path) -> list[str]:
    configured = (player or os.getenv("KOT_CALIBRATION_PLAYER", "")).strip()
    if configured:
        parts = shlex.split(configured)
        if not parts:
            raise ValueError("пустая команда playback")
        replaced = False
        command: list[str] = []
        for part in parts:
            if "{file}" in part:
                command.append(part.replace("{file}", str(signal_path)))
                replaced = True
            else:
                command.append(part)
        if not replaced:
            command.append(str(signal_path))
        return command

    for executable, prefix in (
        ("pw-play", ["pw-play"]),
        ("paplay", ["paplay"]),
        ("aplay", ["aplay", "-q"]),
    ):
        if shutil.which(executable):
            return [*prefix, str(signal_path)]
    raise OSError(
        "не найден pw-play, paplay или aplay; задайте --player 'COMMAND {file}'"
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _safe_label(value: str) -> str:
    result = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value
    )
    result = result.strip("-")
    return result or "source"


def record_fixed_source_sweep(
    output_dir: Path,
    *,
    device: str = "hw:MicArray,0",
    serial_device: str = "/dev/ttyACM0",
    position_deg: float = 0.0,
    source_label: str = "front",
    distance_m: float = 1.0,
    repeats: int = 1,
    pre_roll_seconds: float = 1.0,
    post_roll_seconds: float = 1.0,
    signal_seconds: float = DEFAULT_SIGNAL_SECONDS,
    signal_rms_dbfs: float = DEFAULT_SIGNAL_RMS_DBFS,
    beam_settle_seconds: float = 0.5,
    start_allowance_seconds: float = DEFAULT_START_ALLOWANCE_SECONDS,
    gain: float = 16.0,
    player: str | None = None,
    signal_file: Path | None = None,
    beams: Sequence[str] | str = BEAM_COMMANDS,
    preview: bool = False,
    skip_prompt: bool = False,
    wait_for_ready: Callable[[str], str] = input,
) -> list[Path]:
    """Sweep all fixed beam commands while a speaker stays in one position."""

    if repeats <= 0:
        raise ValueError("repeats должен быть больше нуля")
    for name, value in (
        ("pre_roll_seconds", pre_roll_seconds),
        ("post_roll_seconds", post_roll_seconds),
        ("signal_seconds", signal_seconds),
        ("distance_m", distance_m),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} должен быть больше нуля")
    if beam_settle_seconds < 0 or start_allowance_seconds < 0:
        raise ValueError("временные интервалы не должны быть отрицательными")
    if not math.isfinite(position_deg):
        raise ValueError("position_deg должен быть конечным числом")

    output_dir.mkdir(parents=True, exist_ok=True)
    selected_beams = normalize_beams(beams)
    if signal_file is None:
        signal_path = output_dir / "calibration-signal.wav"
        generate_calibration_signal(
            signal_path,
            duration_seconds=signal_seconds,
            rms_dbfs=signal_rms_dbfs,
        )
    else:
        signal_path = signal_file.expanduser().resolve()
        if not signal_path.is_file():
            raise FileNotFoundError(signal_path)
        signal_seconds = wav_duration_seconds(signal_path)

    player_command = resolve_player_command(player, signal_path)
    total_seconds = math.ceil(
        pre_roll_seconds + signal_seconds + post_roll_seconds + start_allowance_seconds
    )
    safe_label = _safe_label(source_label)

    print("Фиксированный sweep MA-USB8")
    print(f"Источник:      {source_label} / {position_deg:g}° / {distance_m:g} м")
    print(f"Beam:          {' '.join(selected_beams)}")
    print(f"Повторы:       {repeats}")
    print(f"Тестовый WAV:  {signal_path}")
    print(f"Playback:      {shlex.join(player_command)}")
    print(f"Запись/take:   {total_seconds} с")
    print("Массив, колонку, громкость и мебель во время sweep не двигать.")

    if not skip_prompt:
        wait_for_ready("Поставьте колонку в указанную точку и нажмите Enter...")
    if preview:
        print("Предпрослушивание тестового сигнала...")
        subprocess.run(player_command, check=True)
        if not skip_prompt:
            wait_for_ready("Настройте громкость примерно как обычную речь и нажмите Enter...")

    recordings: list[Path] = []
    sequence_index = 0
    for repeat in range(1, repeats + 1):
        # Reverse every second pass to suppress slow drift of speaker volume,
        # temperature and room noise from masquerading as beam directionality.
        order = selected_beams if repeat % 2 else tuple(reversed(selected_beams))
        for beam in order:
            sequence_index += 1
            output = output_dir / f"{safe_label}-beam-{beam}-r{repeat:02d}.wav"
            print(f"\n[{sequence_index}/{len(selected_beams) * repeats}] beam {beam}, repeat {repeat}")
            send_fixed_beam(serial_device, beam)
            if beam_settle_seconds:
                time.sleep(beam_settle_seconds)

            capture = subprocess.Popen(build_record_command(device, total_seconds, output))
            timestamp = datetime.now(timezone.utc).isoformat()
            playback_elapsed = 0.0
            try:
                time.sleep(pre_roll_seconds)
                returncode = capture.poll()
                if returncode is not None:
                    raise subprocess.CalledProcessError(returncode, capture.args)

                playback_started = time.monotonic()
                subprocess.run(player_command, check=True)
                playback_elapsed = time.monotonic() - playback_started

                returncode = capture.wait(timeout=total_seconds + 3.0)
                if returncode:
                    raise subprocess.CalledProcessError(returncode, capture.args)
            except BaseException:
                _stop_process(capture)
                raise

            write_metadata(
                output,
                timestamp=timestamp,
                device=device,
                duration=total_seconds,
                gain=gain,
                beam=beam,
                position_deg=position_deg,
                distance_m=distance_m,
                noise_seconds=pre_roll_seconds,
                beam_settle_seconds=beam_settle_seconds,
                extra={
                    "calibration_type": "fixed_source_beam_sweep",
                    "source_label": source_label,
                    "repeat": repeat,
                    "sequence_index": sequence_index,
                    "pre_roll_seconds": pre_roll_seconds,
                    "post_roll_seconds": post_roll_seconds,
                    "signal_duration_seconds": signal_seconds,
                    "signal_rms_dbfs": signal_rms_dbfs if signal_file is None else None,
                    "signal_file": str(signal_path),
                    "player_command": player_command,
                    "playback_elapsed_seconds": round(playback_elapsed, 4),
                    "player_start_allowance_seconds": start_allowance_seconds,
                },
            )
            recordings.append(output)
            print(f"Сохранено: {output}")

    print(f"\nSweep завершён: {len(recordings)} записей")
    print(f"Сводка: kot-mic-calibrate sweep-summary {output_dir}")
    return recordings


def detect_signal_window(
    samples: np.ndarray,
    sample_rate: int,
    *,
    expected_start_seconds: float,
    signal_duration_seconds: float,
    search_tolerance_seconds: float = DEFAULT_START_ALLOWANCE_SECONDS,
) -> float:
    """Locate playback by maximizing broadband raw-channel energy near expectation."""

    if samples.ndim != 2 or samples.shape[0] == 0:
        raise ValueError("samples must have shape (frames, channels)")
    if sample_rate <= 0 or signal_duration_seconds <= 0:
        raise ValueError("sample_rate and signal duration must be positive")

    block_frames = max(1, round(sample_rate * 0.02))
    block_count = samples.shape[0] // block_frames
    signal_blocks = max(1, round(signal_duration_seconds * sample_rate / block_frames))
    if signal_blocks >= block_count:
        raise ValueError("запись короче тестового сигнала")

    channel_indices = [index for index in RAW_REFERENCE_CHANNELS if index < samples.shape[1]]
    if not channel_indices:
        channel_indices = list(range(samples.shape[1]))
    trimmed = samples[: block_count * block_frames, channel_indices].astype(np.float64)
    trimmed /= 32768.0
    blocks = trimmed.reshape(block_count, block_frames, len(channel_indices))
    channel_energy = np.mean(np.square(blocks), axis=1)
    energy = np.median(channel_energy, axis=1)

    cumulative = np.concatenate(([0.0], np.cumsum(energy)))
    window_energy = cumulative[signal_blocks:] - cumulative[:-signal_blocks]
    expected_block = round(expected_start_seconds * sample_rate / block_frames)
    tolerance_blocks = round(search_tolerance_seconds * sample_rate / block_frames)
    lower = max(0, expected_block - tolerance_blocks)
    upper = min(len(window_energy) - 1, expected_block + tolerance_blocks)
    if upper < lower:
        lower = upper = min(max(expected_block, 0), len(window_energy) - 1)
    relative = int(np.argmax(window_energy[lower : upper + 1]))
    start_block = lower + relative
    return start_block * block_frames / sample_rate


def _segment_metrics(segment: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    normalized = segment.astype(np.float64) / 32768.0
    rms = np.sqrt(np.mean(np.square(normalized), axis=0))
    peak = np.max(np.abs(normalized), axis=0)
    absolute_pcm = np.abs(segment.astype(np.int32))
    clipping = np.mean(absolute_pcm >= 32760, axis=0) * 100.0
    return rms, peak, clipping


def analyze_sweep_take(path: Path) -> SweepTake:
    metadata = read_metadata(path)
    if metadata.get("calibration_type") != "fixed_source_beam_sweep":
        raise ValueError(f"{path.name} не является fixed-source sweep записью")
    beam = metadata.get("beam")
    if not isinstance(beam, str) or beam not in BEAM_COMMANDS:
        raise ValueError(f"Для {path.name} не задан корректный beam")

    sample_rate, samples = read_wav(path)
    if samples.shape[1] <= BEAMFORMED_CHANNEL:
        raise ValueError(f"Для анализа CH6 требуется минимум 7 каналов: {path.name}")

    pre_roll = float(metadata.get("pre_roll_seconds", metadata.get("noise_seconds", 1.0)))
    signal_duration = float(metadata.get("signal_duration_seconds", 0.0))
    tolerance = float(
        metadata.get("player_start_allowance_seconds", DEFAULT_START_ALLOWANCE_SECONDS)
    )
    if pre_roll <= 0 or signal_duration <= 0:
        raise ValueError(f"Некорректные интервалы в {metadata_path(path)}")

    detected_start = detect_signal_window(
        samples,
        sample_rate,
        expected_start_seconds=pre_roll,
        signal_duration_seconds=signal_duration,
        search_tolerance_seconds=tolerance,
    )
    guard = min(0.10, signal_duration * 0.05)
    signal_start = max(0, round((detected_start + guard) * sample_rate))
    signal_end = min(
        len(samples),
        round((detected_start + signal_duration - guard) * sample_rate),
    )
    noise_start = min(round(0.10 * sample_rate), max(0, signal_start // 4))
    noise_end_seconds = min(pre_roll * 0.85, max(0.20, detected_start - 0.15))
    noise_end = min(signal_start, round(noise_end_seconds * sample_rate))
    if noise_end <= noise_start:
        noise_start = 0
        noise_end = max(1, min(signal_start, round(pre_roll * 0.5 * sample_rate)))
    if signal_end <= signal_start or noise_end <= noise_start:
        raise ValueError(f"Не удалось выделить signal/noise окна: {path.name}")

    noise_rms, _, _ = _segment_metrics(samples[noise_start:noise_end])
    signal_rms, signal_peak, signal_clipping = _segment_metrics(
        samples[signal_start:signal_end]
    )

    raw_indices = [index for index in RAW_REFERENCE_CHANNELS if index < samples.shape[1]]
    raw_reference = float(np.median(signal_rms[raw_indices]))
    ch6_noise = float(noise_rms[BEAMFORMED_CHANNEL])
    ch6_signal = float(signal_rms[BEAMFORMED_CHANNEL])
    snr = _dbfs(ch6_signal) - _dbfs(ch6_noise)

    repeat_value = metadata.get("repeat", 1)
    repeat = int(repeat_value) if isinstance(repeat_value, (int, float)) else 1
    position_value = metadata.get("position_deg")
    position = float(position_value) if isinstance(position_value, (int, float)) else None
    source_label = metadata.get("source_label")

    return SweepTake(
        path=path,
        beam=beam,
        repeat=repeat,
        source_label=source_label if isinstance(source_label, str) else "source",
        position_deg=position,
        detected_signal_start_seconds=round(detected_start, 4),
        signal_duration_seconds=signal_duration,
        ch6_noise_dbfs=_dbfs(ch6_noise),
        ch6_signal_dbfs=_dbfs(ch6_signal),
        ch6_snr_db=snr,
        ch6_peak_dbfs=_dbfs(float(signal_peak[BEAMFORMED_CHANNEL])),
        ch6_clipping_percent=float(signal_clipping[BEAMFORMED_CHANNEL]),
        raw_reference_dbfs=_dbfs(raw_reference),
        ch6_vs_raw_db=_dbfs(ch6_signal) - _dbfs(raw_reference),
    )


def _confidence(score_span_db: float, best_vs_opposite_db: float | None) -> str:
    if best_vs_opposite_db is None:
        return "insufficient"
    if score_span_db >= 6.0 and best_vs_opposite_db >= 4.0:
        return "strong"
    if score_span_db >= 3.0 and best_vs_opposite_db >= 2.0:
        return "usable"
    return "weak"


def summarize_fixed_source_sweep(directory: Path) -> SweepReport:
    paths: list[Path] = []
    for path in sorted(directory.glob("*.wav")):
        metadata = read_metadata(path)
        if metadata.get("calibration_type") == "fixed_source_beam_sweep":
            paths.append(path)
    if not paths:
        raise ValueError(f"В {directory} нет fixed-source sweep записей")

    takes = tuple(analyze_sweep_take(path) for path in paths)
    grouped: dict[str, list[SweepTake]] = defaultdict(list)
    for take in takes:
        grouped[take.beam].append(take)

    aggregates: list[BeamAggregate] = []
    for beam in sorted(grouped, key=_beam_sort_key):
        items = grouped[beam]
        aggregates.append(
            BeamAggregate(
                beam=beam,
                count=len(items),
                ch6_signal_dbfs=_mean(item.ch6_signal_dbfs for item in items),
                ch6_signal_std_db=_std(item.ch6_signal_dbfs for item in items),
                ch6_snr_db=_mean(item.ch6_snr_db for item in items),
                ch6_snr_std_db=_std(item.ch6_snr_db for item in items),
                raw_reference_dbfs=_mean(item.raw_reference_dbfs for item in items),
                ch6_vs_raw_db=_mean(item.ch6_vs_raw_db for item in items),
                ch6_vs_raw_std_db=_std(item.ch6_vs_raw_db for item in items),
                ch6_peak_dbfs=max(item.ch6_peak_dbfs for item in items),
                ch6_clipping_percent=max(item.ch6_clipping_percent for item in items),
            )
        )

    beams = tuple(aggregates)
    ranked = sorted(beams, key=lambda item: item.ch6_vs_raw_db, reverse=True)
    best = ranked[0]
    best_snr = max(beams, key=lambda item: item.ch6_snr_db)
    score_span = best.ch6_vs_raw_db - min(item.ch6_vs_raw_db for item in beams)
    best_margin = (
        best.ch6_vs_raw_db - ranked[1].ch6_vs_raw_db if len(ranked) > 1 else 0.0
    )
    opposite = opposite_beam(best.beam)
    opposite_row = next((item for item in beams if item.beam == opposite), None)
    best_vs_opposite = (
        None if opposite_row is None else best.ch6_vs_raw_db - opposite_row.ch6_vs_raw_db
    )
    positions = {take.position_deg for take in takes if take.position_deg is not None}
    labels = {take.source_label for take in takes}

    return SweepReport(
        directory=directory,
        takes=takes,
        beams=beams,
        source_label=next(iter(labels)) if len(labels) == 1 else "mixed",
        position_deg=next(iter(positions)) if len(positions) == 1 else None,
        best_beam=best.beam,
        best_snr_beam=best_snr.beam,
        opposite_beam=opposite_row.beam if opposite_row is not None else None,
        score_span_db=score_span,
        best_vs_opposite_db=best_vs_opposite,
        best_margin_db=best_margin,
        confidence=_confidence(score_span, best_vs_opposite),
    )


def _report_json(report: SweepReport) -> dict[str, object]:
    return {
        "directory": str(report.directory),
        "source_label": report.source_label,
        "position_deg": report.position_deg,
        "best_beam": report.best_beam,
        "best_snr_beam": report.best_snr_beam,
        "opposite_beam": report.opposite_beam,
        "score_span_db": round(report.score_span_db, 4),
        "best_vs_opposite_db": (
            None
            if report.best_vs_opposite_db is None
            else round(report.best_vs_opposite_db, 4)
        ),
        "best_margin_db": round(report.best_margin_db, 4),
        "confidence": report.confidence,
        "beams": [
            {
                "beam": item.beam,
                "count": item.count,
                "ch6_signal_dbfs": round(item.ch6_signal_dbfs, 4),
                "ch6_signal_std_db": round(item.ch6_signal_std_db, 4),
                "ch6_snr_db": round(item.ch6_snr_db, 4),
                "ch6_snr_std_db": round(item.ch6_snr_std_db, 4),
                "raw_reference_dbfs": round(item.raw_reference_dbfs, 4),
                "ch6_vs_raw_db": round(item.ch6_vs_raw_db, 4),
                "ch6_vs_raw_std_db": round(item.ch6_vs_raw_std_db, 4),
                "ch6_peak_dbfs": round(item.ch6_peak_dbfs, 4),
                "ch6_clipping_percent": round(item.ch6_clipping_percent, 6),
            }
            for item in report.beams
        ],
        "takes": [
            {
                "file": item.path.name,
                "beam": item.beam,
                "repeat": item.repeat,
                "detected_signal_start_seconds": item.detected_signal_start_seconds,
                "ch6_signal_dbfs": round(item.ch6_signal_dbfs, 4),
                "ch6_snr_db": round(item.ch6_snr_db, 4),
                "raw_reference_dbfs": round(item.raw_reference_dbfs, 4),
                "ch6_vs_raw_db": round(item.ch6_vs_raw_db, 4),
                "ch6_peak_dbfs": round(item.ch6_peak_dbfs, 4),
                "ch6_clipping_percent": round(item.ch6_clipping_percent, 6),
            }
            for item in report.takes
        ],
    }


def write_sweep_report(report: SweepReport) -> tuple[Path, Path]:
    csv_path = report.directory / "beam-sweep-summary.csv"
    json_path = report.directory / "beam-sweep-summary.json"
    with csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "beam",
                "count",
                "ch6_signal_dbfs",
                "ch6_signal_std_db",
                "ch6_snr_db",
                "ch6_snr_std_db",
                "raw_reference_dbfs",
                "ch6_vs_raw_db",
                "ch6_vs_raw_std_db",
                "ch6_peak_dbfs",
                "ch6_clipping_percent",
            ],
        )
        writer.writeheader()
        for item in report.beams:
            writer.writerow(
                {
                    "beam": item.beam,
                    "count": item.count,
                    "ch6_signal_dbfs": f"{item.ch6_signal_dbfs:.4f}",
                    "ch6_signal_std_db": f"{item.ch6_signal_std_db:.4f}",
                    "ch6_snr_db": f"{item.ch6_snr_db:.4f}",
                    "ch6_snr_std_db": f"{item.ch6_snr_std_db:.4f}",
                    "raw_reference_dbfs": f"{item.raw_reference_dbfs:.4f}",
                    "ch6_vs_raw_db": f"{item.ch6_vs_raw_db:.4f}",
                    "ch6_vs_raw_std_db": f"{item.ch6_vs_raw_std_db:.4f}",
                    "ch6_peak_dbfs": f"{item.ch6_peak_dbfs:.4f}",
                    "ch6_clipping_percent": f"{item.ch6_clipping_percent:.6f}",
                }
            )
    json_path.write_text(
        json.dumps(_report_json(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return csv_path, json_path


def print_sweep_report(report: SweepReport, *, write_files: bool = True) -> None:
    scores = [item.ch6_vs_raw_db for item in report.beams]
    minimum = min(scores)
    maximum = max(scores)
    span = max(1e-9, maximum - minimum)

    print(f"Каталог: {report.directory}")
    print(
        f"Источник: {report.source_label}; положение: "
        f"{report.position_deg if report.position_deg is not None else '-'}°"
    )
    print("score = CH6 signal RMS - median raw CH0..CH5/CH7")
    print("beam  n  CH6 signal    SNR   score   std  peak  clip     диаграмма")
    for item in report.beams:
        bar_length = round((item.ch6_vs_raw_db - minimum) / span * 24)
        bar = "#" * bar_length
        print(
            f" {item.beam:>2}  {item.count:>2}  {item.ch6_signal_dbfs:9.2f} "
            f"{item.ch6_snr_db:6.2f} {item.ch6_vs_raw_db:7.2f} "
            f"{item.ch6_vs_raw_std_db:5.2f} {item.ch6_peak_dbfs:5.1f} "
            f"{item.ch6_clipping_percent:6.3f}%  {bar}"
        )

    opposite_delta = (
        "n/a"
        if report.best_vs_opposite_db is None
        else f"{report.best_vs_opposite_db:.2f} dB"
    )
    print()
    print(f"Лучший beam по нормализованному уровню: {report.best_beam}")
    print(f"Лучший beam по rough SNR:             {report.best_snr_beam}")
    print(f"Размах диаграммы:                     {report.score_span_db:.2f} dB")
    print(f"Лучший - противоположный:             {opposite_delta}")
    print(f"Отрыв от второго места:               {report.best_margin_db:.2f} dB")
    print(f"Оценка направленности:                 {report.confidence}")
    if report.confidence in {"weak", "insufficient"}:
        print("Вывод: геометрию beam по этому sweep применять рано; сигнал направлен слабо.")
    elif report.best_beam != report.best_snr_beam:
        print("Предупреждение: лучший уровень и лучший SNR не совпали; сделайте 2–3 повтора.")

    if write_files:
        csv_path, json_path = write_sweep_report(report)
        print(f"CSV:  {csv_path}")
        print(f"JSON: {json_path}")


def circular_sector_distance(first: int, second: int) -> int:
    difference = abs(first - second) % 12
    return min(difference, 12 - difference)


def fit_mapping_observations(
    observations: Sequence[tuple[float, int, float]],
) -> dict[str, object]:
    if not observations:
        raise ValueError("нет наблюдений для fit-map")

    candidates: list[dict[str, object]] = []
    for clockwise in (True, False):
        direction = 1 if clockwise else -1
        for offset in range(12):
            weighted_error = 0.0
            weight_sum = 0.0
            predictions: list[dict[str, object]] = []
            for position_deg, observed_beam, weight in observations:
                position_sector = round(position_deg / 30.0) % 12
                predicted = (direction * position_sector + offset) % 12
                distance = circular_sector_distance(predicted, observed_beam)
                effective_weight = max(0.01, weight)
                weighted_error += effective_weight * distance * distance
                weight_sum += effective_weight
                predictions.append(
                    {
                        "position_deg": position_deg,
                        "observed_beam": BEAM_COMMANDS[observed_beam],
                        "predicted_beam": BEAM_COMMANDS[predicted],
                        "error_sectors": distance,
                    }
                )
            mean_error = math.sqrt(weighted_error / weight_sum)
            candidates.append(
                {
                    "clockwise": clockwise,
                    "offset": offset,
                    "mean_error_sectors": mean_error,
                    "predictions": predictions,
                }
            )

    candidates.sort(key=lambda item: float(item["mean_error_sectors"]))
    best = candidates[0]
    runner_up = candidates[1]
    gap = float(runner_up["mean_error_sectors"]) - float(best["mean_error_sectors"])
    distinct_positions = len({round(item[0] / 30.0) % 12 for item in observations})
    ambiguous = abs(gap) < 1e-9
    mean_error = float(best["mean_error_sectors"])
    if distinct_positions >= 4 and mean_error <= 0.35 and not ambiguous:
        confidence = "strong"
    elif distinct_positions >= 3 and mean_error <= 0.75 and not ambiguous:
        confidence = "usable"
    else:
        confidence = "weak"

    return {
        **best,
        "runner_up_error_sectors": float(runner_up["mean_error_sectors"]),
        "candidate_gap_sectors": gap,
        "distinct_positions": distinct_positions,
        "ambiguous": ambiguous,
        "confidence": confidence,
    }


def fit_beam_mapping(directories: Sequence[Path]) -> dict[str, object]:
    if len(directories) < 2:
        raise ValueError("для fit-map нужны sweep минимум из двух физических положений")

    reports = [summarize_fixed_source_sweep(directory) for directory in directories]
    observations: list[tuple[float, int, float]] = []
    details: list[dict[str, object]] = []
    for report in reports:
        if report.position_deg is None:
            raise ValueError(f"В {report.directory} нет единственного position_deg")
        observed = BEAM_COMMANDS.index(report.best_beam)
        weight = max(0.5, report.score_span_db)
        observations.append((report.position_deg, observed, weight))
        details.append(
            {
                "directory": str(report.directory),
                "position_deg": report.position_deg,
                "best_beam": report.best_beam,
                "score_span_db": report.score_span_db,
                "confidence": report.confidence,
            }
        )

    result = fit_mapping_observations(observations)
    result["observations"] = details
    return result


def print_beam_mapping(result: dict[str, object], *, output: Path | None = None) -> None:
    print("Калибровка physical angle -> MA-USB8 beam")
    print(f"Положений:       {result['distinct_positions']}")
    print(f"Средняя ошибка:  {float(result['mean_error_sectors']):.3f} сектора")
    print(f"Направление:     {'clockwise' if result['clockwise'] else 'counterclockwise'}")
    print(f"Offset:          {result['offset']}")
    print(f"Уверенность:     {result['confidence']}")
    print()
    for item in result["predictions"]:  # type: ignore[assignment]
        print(
            f"{float(item['position_deg']):6.1f}°: observed={item['observed_beam']} "
            f"predicted={item['predicted_beam']} error={item['error_sectors']}"
        )

    if result["confidence"] in {"strong", "usable"}:
        print()
        print("Рекомендуемые настройки:")
        print(f"KOT_AUTO_BEAM_OFFSET={result['offset']}")
        print(f"KOT_AUTO_BEAM_CLOCKWISE={1 if result['clockwise'] else 0}")
    else:
        print()
        print("Настройки автоматически не применять: нужны ещё позиции или сильнее диаграмма.")

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"JSON: {output}")
