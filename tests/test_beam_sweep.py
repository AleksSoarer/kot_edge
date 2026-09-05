from __future__ import annotations

import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from kot.beam_sweep import (
    analyze_sweep_take,
    detect_signal_window,
    fit_mapping_observations,
    generate_calibration_signal,
    normalize_beams,
    record_fixed_source_sweep,
    resolve_player_command,
    summarize_fixed_source_sweep,
    wav_duration_seconds,
    write_sweep_report,
)
from kot.calibrate import metadata_path


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(samples.shape[1] if samples.ndim == 2 else 1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.astype("<i2").tobytes())


def test_generated_signal_is_deterministic_and_has_headroom(tmp_path: Path) -> None:
    first = generate_calibration_signal(tmp_path / "first.wav", duration_seconds=0.5)
    second = generate_calibration_signal(tmp_path / "second.wav", duration_seconds=0.5)

    assert first.read_bytes() == second.read_bytes()
    assert wav_duration_seconds(first) == pytest.approx(0.5, abs=1 / 48_000)
    with wave.open(str(first), "rb") as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    peak = np.max(np.abs(samples.astype(np.float64))) / 32768.0
    rms = np.sqrt(np.mean(np.square(samples.astype(np.float64) / 32768.0)))
    assert 20 * math.log10(peak) <= -2.9
    assert -24.0 < 20 * math.log10(rms) < -15.0


def test_player_command_supports_placeholder_and_append(tmp_path: Path) -> None:
    signal = tmp_path / "signal.wav"
    assert resolve_player_command("aplay -q -D default {file}", signal) == [
        "aplay",
        "-q",
        "-D",
        "default",
        str(signal),
    ]
    assert resolve_player_command("paplay", signal) == ["paplay", str(signal)]


def test_normalize_beams_accepts_compact_and_csv_forms() -> None:
    assert normalize_beams("0369") == ("0", "3", "6", "9")
    assert normalize_beams("0, 3, 6, 9") == ("0", "3", "6", "9")
    with pytest.raises(ValueError):
        normalize_beams("0X")


def test_detect_signal_window_finds_delayed_broadband_source() -> None:
    sample_rate = 1000
    rng = np.random.default_rng(7)
    samples = rng.normal(0, 20, size=(4000, 8))
    samples[1370:2370] += rng.normal(0, 1200, size=(1000, 8))

    start = detect_signal_window(
        samples.astype(np.int16),
        sample_rate,
        expected_start_seconds=1.0,
        signal_duration_seconds=1.0,
        search_tolerance_seconds=1.0,
    )

    assert start == pytest.approx(1.36, abs=0.04)


def _make_sweep_take(
    directory: Path,
    beam: str,
    response: float,
    *,
    repeat: int = 1,
    position_deg: float = 0.0,
) -> Path:
    sample_rate = 1000
    frames = 4000
    signal_start = 1250
    signal_end = 2250
    rng = np.random.default_rng(ord(beam) + repeat * 100)
    samples = rng.normal(0, 50, size=(frames, 8))
    source = rng.normal(0, 1400, size=signal_end - signal_start)
    for channel in (0, 1, 2, 3, 4, 5, 7):
        samples[signal_start:signal_end, channel] += source
    samples[signal_start:signal_end, 6] += source * response
    samples = np.clip(samples, -32000, 32000).astype(np.int16)

    path = directory / f"front-beam-{beam}-r{repeat:02d}.wav"
    _write_wav(path, samples, sample_rate)
    metadata_path(path).write_text(
        json.dumps(
            {
                "calibration_type": "fixed_source_beam_sweep",
                "beam": beam,
                "repeat": repeat,
                "source_label": "front",
                "position_deg": position_deg,
                "pre_roll_seconds": 1.0,
                "noise_seconds": 1.0,
                "signal_duration_seconds": 1.0,
                "player_start_allowance_seconds": 1.0,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_sweep_summary_finds_best_beam_and_writes_machine_readable_report(
    tmp_path: Path,
) -> None:
    responses = {beam: 0.8 for beam in "0123456789AB"}
    responses["3"] = 2.0
    responses["2"] = 1.6
    responses["4"] = 1.5
    responses["9"] = 0.35
    for beam, response in responses.items():
        _make_sweep_take(tmp_path, beam, response)

    take = analyze_sweep_take(tmp_path / "front-beam-3-r01.wav")
    assert take.detected_signal_start_seconds == pytest.approx(1.24, abs=0.05)
    assert take.ch6_vs_raw_db > 5.0

    report = summarize_fixed_source_sweep(tmp_path)
    assert report.best_beam == "3"
    assert report.opposite_beam == "9"
    assert report.best_vs_opposite_db is not None
    assert report.best_vs_opposite_db > 10.0
    assert report.confidence == "strong"

    csv_path, json_path = write_sweep_report(report)
    assert csv_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["best_beam"] == "3"
    assert len(payload["beams"]) == 12


def test_mapping_fit_recovers_clockwise_offset() -> None:
    result = fit_mapping_observations(
        [
            (0.0, 2, 8.0),
            (90.0, 5, 8.0),
            (180.0, 8, 8.0),
            (270.0, 11, 8.0),
        ]
    )
    assert result["clockwise"] is True
    assert result["offset"] == 2
    assert result["mean_error_sectors"] == pytest.approx(0.0)
    assert result["confidence"] == "strong"


def test_fixed_source_sweep_reverses_second_pass_and_writes_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal = tmp_path / "source.wav"
    _write_wav(signal, np.zeros(100, dtype=np.int16), 100)
    sent: list[str] = []
    metadata_calls: list[dict[str, object]] = []
    playback_calls: list[list[str]] = []

    class FakeCapture:
        args = ["arecord"]

        def poll(self):
            return None

        def wait(self, timeout: float):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr("kot.beam_sweep.send_fixed_beam", lambda _, beam: sent.append(beam))
    monkeypatch.setattr("kot.beam_sweep.subprocess.Popen", lambda command: FakeCapture())
    monkeypatch.setattr(
        "kot.beam_sweep.subprocess.run",
        lambda command, check: playback_calls.append(command),
    )
    monkeypatch.setattr("kot.beam_sweep.time.sleep", lambda _: None)
    monotonic_values = iter([1.0, 1.2, 2.0, 2.2, 3.0, 3.2, 4.0, 4.2])
    monkeypatch.setattr("kot.beam_sweep.time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        "kot.beam_sweep.write_metadata",
        lambda output, **kwargs: metadata_calls.append({"output": output, **kwargs}),
    )

    recordings = record_fixed_source_sweep(
        tmp_path / "run",
        signal_file=signal,
        player="paplay {file}",
        beams="01",
        repeats=2,
        skip_prompt=True,
    )

    assert sent == ["0", "1", "1", "0"]
    assert len(recordings) == 4
    assert len(playback_calls) == 4
    assert [call["extra"]["repeat"] for call in metadata_calls] == [1, 1, 2, 2]
