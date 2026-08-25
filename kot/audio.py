from __future__ import annotations


def build_ffmpeg_aec_command(
    *,
    monitor_source: str,
    mic_source: str,
    mic_sample_rate: int,
    mic_channels: int,
    beam_channel: int,
    output_sample_rate: int,
    filter_order: int,
    mu: float,
    leakage: float,
) -> list[str]:
    """Build a passive PulseAudio/PipeWire capture with an NLMS echo canceller.

    ``beam_channel`` uses the one-based convention of SoX ``remix`` while
    FFmpeg's ``pan`` filter addresses channels as c0, c1, ...
    """
    channel = beam_channel - 1
    if not 0 <= channel < mic_channels:
        raise ValueError(
            f"beam_channel must be between 1 and {mic_channels}, got {beam_channel}"
        )
    if filter_order < 1:
        raise ValueError("filter_order must be positive")

    graph = (
        f"[0:a]pan=mono|c0=0.5*c0+0.5*c1,aresample={output_sample_rate}[ref];"
        f"[1:a]pan=mono|c0=c{channel},aresample={output_sample_rate}[mic];"
        f"[ref][mic]anlms=order={filter_order}:mu={mu}:"
        f"leakage={leakage}:out_mode=o[aec]"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-thread_queue_size",
        "1024",
        "-f",
        "pulse",
        "-sample_rate",
        str(mic_sample_rate),
        "-channels",
        "2",
        "-i",
        monitor_source,
        "-thread_queue_size",
        "1024",
        "-f",
        "pulse",
        "-sample_rate",
        str(mic_sample_rate),
        "-channels",
        str(mic_channels),
        "-i",
        mic_source,
        "-filter_complex",
        graph,
        "-map",
        "[aec]",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-",
    ]
