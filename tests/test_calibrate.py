from pathlib import Path

import numpy as np

from kot.calibrate import analyze_samples, build_record_command


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
