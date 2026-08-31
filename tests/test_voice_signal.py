from __future__ import annotations

import importlib
import sys
import types

import numpy as np
import pytest


@pytest.fixture
def voice_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx"))
    sys.modules.pop("kot.voice", None)
    module = importlib.import_module("kot.voice")
    monkeypatch.setattr(module, "GAIN", 1.0)
    yield module
    sys.modules.pop("kot.voice", None)


def test_pcm_routes_wake_and_command_from_different_channels(voice_module) -> None:
    frames = np.zeros((3, 8), dtype="<i2")
    frames[:, 6] = [1000, 2000, 3000]
    frames[:, 7] = [-4000, -5000, -6000]

    command, wake, levels = voice_module.pcm_to_float(
        frames.tobytes(),
        channels=8,
        selected_channel=6,
        wake_channel=7,
    )

    assert command == pytest.approx(frames[:, 6] / 32768.0)
    assert wake == pytest.approx(frames[:, 7] / 32768.0)
    assert len(levels) == 8


def test_pcm_reuses_array_when_wake_and_command_channel_match(voice_module) -> None:
    frames = np.zeros((2, 8), dtype="<i2")
    command, wake, _ = voice_module.pcm_to_float(
        frames.tobytes(),
        channels=8,
        selected_channel=6,
        wake_channel=6,
    )

    assert wake is command


def test_signal_metrics_reports_silence_and_known_level(voice_module) -> None:
    assert voice_module.signal_metrics(np.zeros(4, dtype=np.float32)) == (-120.0, -120.0)
    rms_dbfs, peak_dbfs = voice_module.signal_metrics(
        np.array([0.5, -0.5], dtype=np.float32)
    )
    assert rms_dbfs == pytest.approx(-6.02, abs=0.01)
    assert peak_dbfs == pytest.approx(-6.02, abs=0.01)


def test_aec_pipeline_reports_physical_source_channel(voice_module) -> None:
    pipeline = voice_module.CapturePipeline(
        stream=None,
        processes=(),
        backend="AEC",
        channels=1,
        selected_channel=0,
        wake_channel=0,
        meter_channel=3,
    )

    assert pipeline.command_source_channel == 3
    assert pipeline.wake_source_channel == 3
