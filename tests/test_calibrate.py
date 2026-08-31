import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from kot.calibrate import (
    analyze_noise_and_speech,
    analyze_samples,
    beam_for_position,
    build_record_command,
    metadata_path,
    record,
    record_circle,
    split_channels,
    summarize_directory,
)


def test_analyze_silent_channels() -> None:
    result = analyze_samples(np.zeros((100, 8), dtype=np.int16))
    assert len(result) == 8
    assert result[6].channel == 6
    assert result[6].rms_dbfs == -120.0
    assert result[6].clipping_percent == 0.0


def test_analyze_reports_clipping() -> None:
    samples = np.zeros((4, 2), dtype=np.int16)
    samples[0, 1] = 32767
    result = analyze_samples(samples)
    assert result[1].clipping_percent == 25.0


def test_analyze_applies_voice_gain() -> None:
    samples = np.full((4, 1), 8192, dtype=np.int16)
    result = analyze_samples(samples, gain=8.0)
    assert result[0].clipping_percent == 100.0
    assert result[0].peak_dbfs == 0.0


def test_arecord_duration_is_an_integer() -> None:
    command = build_record_command("hw:MicArray,0", 15, Path("test.wav"))
    duration_index = command.index("-d") + 1
    assert command[duration_index] == "15"


def test_analyze_noise_and_speech_reports_raw_rms_and_rough_snr() -> None:
    samples = np.vstack(
        (
            np.full((100, 2), 1000, dtype=np.int16),
            np.full((100, 2), 2000, dtype=np.int16),
        )
    )
    result = analyze_noise_and_speech(samples, sample_rate=100, noise_seconds=1.0)
    assert result[0].noise_rms_dbfs == pytest.approx(20 * math.log10(1000 / 32768))
    assert result[0].speech_rms_dbfs == pytest.approx(20 * math.log10(2000 / 32768))
    assert result[0].snr_db == pytest.approx(20 * math.log10(2))


def test_record_sets_beam_and_writes_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "kot.calibrate.send_fixed_beam",
        lambda device, beam: calls.append((device, beam)),
    )
    monkeypatch.setattr(
        "kot.calibrate.subprocess.run",
        lambda command, check: commands.append(command),
    )
    sleeps: list[float] = []
    monkeypatch.setattr("kot.calibrate.time.sleep", sleeps.append)
    output = tmp_path / "position.wav"

    record(
        "hw:test,0",
        10,
        output,
        gain=8.0,
        beam="a",
        serial_device="/dev/test-array",
        position_deg=30.0,
        distance_m=1.5,
        noise_seconds=2.0,
    )

    assert calls == [("/dev/test-array", "A")]
    assert sleeps == [0.3]
    assert commands[0][-1] == str(output)
    metadata = json.loads(metadata_path(output).read_text(encoding="utf-8"))
    assert metadata["device"] == "hw:test,0"
    assert metadata["duration"] == 10
    assert metadata["gain"] == 8.0
    assert metadata["beam"] == "A"
    assert metadata["position_deg"] == 30.0
    assert metadata["distance_m"] == 1.5
    assert metadata["noise_seconds"] == 2.0
    assert metadata["beam_settle_seconds"] == 0.3
    assert metadata["timestamp"].endswith("+00:00")


def test_beam_for_position_supports_orientation_and_offset() -> None:
    assert beam_for_position(0) == "0"
    assert beam_for_position(300) == "A"
    assert beam_for_position(30, offset=2) == "3"
    assert beam_for_position(30, clockwise=False) == "B"


def test_circle_records_aligned_and_opposite_beams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captures: list[tuple[Path, str | None, float | None]] = []

    def fake_record(
        device: str,
        seconds: int,
        output: Path,
        **kwargs: object,
    ) -> None:
        captures.append(
            (output, kwargs.get("beam"), kwargs.get("position_deg"))  # type: ignore[arg-type]
        )

    monkeypatch.setattr("kot.calibrate.record", fake_record)
    prompts: list[str] = []
    result = record_circle(
        tmp_path,
        device="hw:test,0",
        serial_device="/dev/test-array",
        seconds=10,
        gain=8.0,
        distance_m=1.0,
        noise_seconds=2.0,
        opposite=True,
        wait_for_position=lambda message: prompts.append(message) or "",
    )

    assert len(prompts) == 24
    assert len(result) == 24
    assert captures[0][1:] == ("0", 0.0)
    assert captures[1][1:] == ("6", 0.0)
    assert captures[-2][1:] == ("B", 330.0)
    assert captures[-1][1:] == ("5", 330.0)


def test_split_channels_writes_mono_wavs(tmp_path: Path) -> None:
    source = tmp_path / "array.wav"
    samples = np.array([[1, 10], [2, 20], [3, 30]], dtype=np.int16)
    with wave.open(str(source), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(samples.astype("<i2").tobytes())

    outputs = split_channels(source)

    assert [path.name for path in outputs] == ["array-CH0.wav", "array-CH1.wav"]
    with wave.open(str(outputs[1]), "rb") as channel:
        assert channel.getnchannels() == 1
        assert channel.getframerate() == 16000
        assert np.frombuffer(channel.readframes(3), dtype="<i2").tolist() == [10, 20, 30]


def test_summarize_directory_uses_sidecar_noise_and_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aligned = tmp_path / "pos-000-aligned-beam-0.wav"
    opposite = tmp_path / "pos-000-opposite-beam-6.wav"
    aligned.touch()
    opposite.touch()
    for path, beam in ((aligned, "0"), (opposite, "6")):
        metadata_path(path).write_text(
            json.dumps({"position_deg": 0, "beam": beam, "noise_seconds": 1.0}),
            encoding="utf-8",
        )

    noise = np.full((100, 8), 1000, dtype=np.int16)
    speech = np.full((100, 8), 2000, dtype=np.int16)
    monkeypatch.setattr(
        "kot.calibrate.read_wav",
        lambda path: (100, np.vstack((noise, speech if path == aligned else noise))),
    )

    rows = summarize_directory(tmp_path)

    assert [row.role for row in rows] == ["aligned", "opposite"]
    assert rows[0].beam == "0"
    assert rows[0].snr_db[6] == pytest.approx(20 * math.log10(2))
    assert rows[1].snr_db[6] == pytest.approx(0.0)
