import pytest

from kot.audio import build_ffmpeg_aec_command


def command(beam_channel: int = 7) -> list[str]:
    return build_ffmpeg_aec_command(
        monitor_source="music.monitor",
        mic_source="mic.8ch",
        mic_sample_rate=48000,
        mic_channels=8,
        beam_channel=beam_channel,
        output_sample_rate=16000,
        filter_order=2048,
        mu=0.25,
        leakage=0.0001,
    )


def test_aec_capture_is_passive_and_selects_one_based_beam_channel() -> None:
    result = command(7)
    graph = result[result.index("-filter_complex") + 1]

    assert result.count("-f") == 3
    assert "pulse" in result
    assert "music.monitor" in result
    assert "mic.8ch" in result
    assert "c0=c6" in graph
    assert "anlms=order=2048:mu=0.25:leakage=0.0001:out_mode=o" in graph
    assert "sink" not in " ".join(result).lower()


@pytest.mark.parametrize("beam_channel", [0, 9])
def test_aec_capture_rejects_invalid_channel(beam_channel: int) -> None:
    with pytest.raises(ValueError, match="beam_channel"):
        command(beam_channel)
