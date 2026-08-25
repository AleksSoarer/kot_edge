from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np
import sherpa_onnx

from .mic_led import MicArrayLeds
from .music import PlayerController
from .ui import (
    ACK_TEXT,
    AUDIO_UNAVAILABLE_TEXT,
    MIC_STREAM_STOPPED_TEXT,
    MIC_UNAVAILABLE_TEXT,
)
from .wake import NpuWakeDetector

BASE = Path(os.getenv("KOT_VOICE_BASE", str(Path.home() / "voice-assistant"))).expanduser()
MODEL = BASE / "sherpa-onnx-small-zipformer-ru-2024-09-18"
VAD_MODEL = BASE / "silero_vad.onnx"

EDGE_URL = os.getenv("KOT_EDGE_URL", "http://127.0.0.1:8765").rstrip("/")
EDGE_TOKEN = os.getenv("KOT_EDGE_TOKEN")

MIC_DEVICE = os.getenv("KOT_MIC_DEVICE", "hw:MicArray,0")
MIC_SAMPLE_RATE = 48000
MIC_CHANNELS = 8
SOX_BEAM_CHANNEL = int(os.getenv("KOT_MIC_BEAM_CHANNEL", "7"))
SAMPLE_RATE = 16000
GAIN = float(os.getenv("KOT_MIC_GAIN", "16.0"))

ASR_THREADS = int(os.getenv("KOT_ASR_THREADS", "2"))

# Small Zipformer sometimes recognizes "кот" as "код".
WAKE_PHRASES = (
    "эй кот",
    "эй код",
    "ей кот",
    "ей код",
)
WAKE_WINDOW_SECONDS = 3.0
WAKE_DECODE_INTERVAL = 0.8
WAKE_MIN_AUDIO_SECONDS = 1.2
WAKE_COOLDOWN_SECONDS = 1.0
DEBUG_WAKE_ASR = os.getenv("KOT_DEBUG_WAKE_ASR", "0") == "1"
WAKE_BACKEND = os.getenv("KOT_WAKE_BACKEND", "asr").strip().lower()
NPU_WAKE_COMMAND = os.getenv("KOT_NPU_WAKE_COMMAND", "").strip()
MIC_LED_DEVICE = os.getenv("KOT_MIC_LED_DEVICE", "/dev/ttyACM0").strip()
MIC_LED_ON_WAKE = os.getenv("KOT_MIC_LED_ON_WAKE", "1") == "1"
MUSIC_WAKE_MUTE_SECONDS = float(os.getenv("KOT_MUSIC_WAKE_MUTE_SECONDS", "3.0"))

COMMAND_TIMEOUT_SECONDS = 8.0
COMMAND_TIMEOUT_AFTER_WAKE_ONLY = 8.0
COMMAND_PREROLL_SECONDS = 3.0

WINDOW_SIZE = 512
VAD_THRESHOLD = 0.25
VAD_MIN_SILENCE = 0.50
VAD_MIN_SPEECH = 0.25
VAD_MAX_SPEECH = 12.0


class EdgeClient:
    def __init__(self, base_url: str = EDGE_URL, token: str | None = EDGE_TOKEN) -> None:
        self.base_url = base_url
        self.token = token
        self._warned = False

    def patch(self, **changes: object) -> None:
        payload = json.dumps(changes, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Kot-Token"] = self.token

        request = Request(
            f"{self.base_url}/api/status",
            data=payload,
            headers=headers,
            method="PATCH",
        )

        try:
            with urlopen(request, timeout=1.5) as response:
                response.read()
            self._warned = False
        except (OSError, URLError) as exc:
            if not self._warned:
                print(f"Kot Edge API недоступен: {exc}", file=sys.stderr)
                self._warned = True


def create_recognizer() -> sherpa_onnx.OfflineRecognizer:
    print("Загрузка ASR...")
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(MODEL / "encoder.int8.onnx"),
        decoder=str(MODEL / "decoder.onnx"),
        joiner=str(MODEL / "joiner.int8.onnx"),
        tokens=str(MODEL / "tokens.txt"),
        num_threads=ASR_THREADS,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        debug=False,
    )
    print("ASR загружен")
    return recognizer


def create_vad(verbose: bool = True) -> sherpa_onnx.VoiceActivityDetector:
    if verbose:
        print("Загрузка VAD...")

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(VAD_MODEL)
    config.silero_vad.threshold = VAD_THRESHOLD
    config.silero_vad.min_silence_duration = VAD_MIN_SILENCE
    config.silero_vad.min_speech_duration = VAD_MIN_SPEECH
    config.silero_vad.max_speech_duration = VAD_MAX_SPEECH
    config.sample_rate = SAMPLE_RATE
    config.num_threads = 1

    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
    if verbose:
        print("VAD загружен")
    return vad


def reset_vad(vad: sherpa_onnx.VoiceActivityDetector) -> sherpa_onnx.VoiceActivityDetector:
    reset = getattr(vad, "reset", None)
    if callable(reset):
        reset()
        return vad
    return create_vad(verbose=False)


def start_capture() -> tuple[subprocess.Popen[bytes], subprocess.Popen[bytes]]:
    arecord_cmd = [
        "arecord",
        "-q",
        "-D",
        MIC_DEVICE,
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-r",
        str(MIC_SAMPLE_RATE),
        "-c",
        str(MIC_CHANNELS),
    ]
    arecord = subprocess.Popen(arecord_cmd, stdout=subprocess.PIPE, stderr=None, bufsize=0)

    sox_cmd = [
        "sox",
        "-q",
        "-t",
        "raw",
        "-e",
        "signed-integer",
        "-b",
        "16",
        "-L",
        "-r",
        str(MIC_SAMPLE_RATE),
        "-c",
        str(MIC_CHANNELS),
        "-",
        "-t",
        "raw",
        "-e",
        "signed-integer",
        "-b",
        "16",
        "-L",
        "-r",
        str(SAMPLE_RATE),
        "-c",
        "1",
        "-",
        "remix",
        str(SOX_BEAM_CHANNEL),
    ]
    sox = subprocess.Popen(
        sox_cmd,
        stdin=arecord.stdout,
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=0,
    )
    assert arecord.stdout is not None
    arecord.stdout.close()
    return arecord, sox


def read_exact(stream, size: int) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def pcm_to_float(raw: bytes) -> np.ndarray:
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    samples /= 32768.0
    samples *= GAIN
    np.clip(samples, -1.0, 1.0, out=samples)
    return samples


def recognize(
    recognizer: sherpa_onnx.OfflineRecognizer,
    samples: np.ndarray,
) -> tuple[str, float, float]:
    if len(samples) == 0:
        return "", 0.0, 0.0

    duration = len(samples) / SAMPLE_RATE
    stream = recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    started = time.monotonic()
    recognizer.decode_stream(stream)
    elapsed = time.monotonic() - started
    text = stream.result.text.strip()
    rtf = elapsed / duration if duration > 0 else 0.0
    return text, elapsed, rtf


def normalize_text(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def find_wake(text: str) -> tuple[bool, str, str]:
    normalized = normalize_text(text)
    best_position: int | None = None
    best_phrase: str | None = None

    for phrase in WAKE_PHRASES:
        pos = normalized.find(phrase)
        if pos < 0:
            continue
        if best_position is None or pos < best_position:
            best_position = pos
            best_phrase = phrase

    if best_position is None or best_phrase is None:
        return False, normalized, ""

    command_start = best_position + len(best_phrase)
    return True, normalized, normalized[command_start:].strip()


def strip_wake_from_command(text: str) -> str:
    found, normalized, command = find_wake(text)
    return command if found else normalized


def append_rolling(buffer: np.ndarray, samples: np.ndarray, max_samples: int) -> np.ndarray:
    if buffer.size == 0:
        buffer = samples.copy()
    else:
        buffer = np.concatenate((buffer, samples))
    if len(buffer) > max_samples:
        buffer = buffer[-max_samples:]
    return buffer


def process_vad_segments(
    vad: sherpa_onnx.VoiceActivityDetector,
    recognizer: sherpa_onnx.OfflineRecognizer,
    edge: EdgeClient,
    music: PlayerController,
    music_was_playing: bool,
) -> tuple[bool, bool]:
    command_done = False
    wake_only_detected = False

    while not vad.empty():
        segment = np.asarray(vad.front.samples, dtype=np.float32).copy()
        vad.pop()
        duration = len(segment) / SAMPLE_RATE
        text, elapsed, rtf = recognize(recognizer, segment)
        normalized = normalize_text(text)
        if not normalized:
            continue

        print(f"[ACTIVE-ASR] {normalized}")
        command = strip_wake_from_command(normalized)

        if not command:
            wake_only_detected = True
            edge.patch(mode="listening", heard_text=normalized)
            continue

        found, _, after = find_wake(command)
        if found and not after:
            wake_only_detected = True
            edge.patch(mode="listening", heard_text=normalized)
            continue

        print()
        print(f"[COMMAND] {command}")
        print(f"          audio={duration:.2f} c | decode={elapsed:.2f} c | RTF={rtf:.2f}")
        print()

        music_result = music.finish_command(command, music_was_playing)
        music_snapshot = music_result.snapshot

        # The transcript is sent only after wake was detected, so the UI never shows
        # unrelated room speech while Kot is idle. Music metadata is published
        # immediately; the server also keeps it fresh in the background.
        edge.patch(
            mode="music" if music_snapshot.playing else "idle",
            heard_text=command,
            reply_text=music_result.reply_text or ACK_TEXT,
            mic_online=True,
            music_playing=music_snapshot.playing,
            music_available=music_snapshot.available,
            music_status=music_snapshot.status,
            track=music_snapshot.track,
            artist=music_snapshot.artist,
            volume=music_snapshot.volume,
        )
        command_done = True
        break

    return command_done, wake_only_detected


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    required_files = [
        MODEL / "encoder.int8.onnx",
        MODEL / "decoder.onnx",
        MODEL / "joiner.int8.onnx",
        MODEL / "tokens.txt",
        VAD_MODEL,
    ]
    for path in required_files:
        if not path.exists():
            print(f"Не найден файл: {path}", file=sys.stderr)
            return 1

    edge = EdgeClient()
    music = PlayerController()
    mic_leds = MicArrayLeds(MIC_LED_DEVICE, enabled=MIC_LED_ON_WAKE)
    mic_leds.set_listening(False)
    recognizer = create_recognizer()
    vad = create_vad()
    print("Запуск MA-USB8...")
    try:
        arecord, capture = start_capture()
    except OSError as exc:
        edge.patch(mic_online=False, mode="error", message=MIC_UNAVAILABLE_TEXT)
        print(f"Не удалось запустить arecord/sox: {exc}", file=sys.stderr)
        return 1

    time.sleep(0.3)
    if arecord.poll() is not None:
        edge.patch(mic_online=False, mode="error", message=MIC_UNAVAILABLE_TEXT)
        print(f"arecord завершился с кодом {arecord.returncode}", file=sys.stderr)
        return 1
    if capture.poll() is not None:
        edge.patch(mic_online=False, mode="error", message=AUDIO_UNAVAILABLE_TEXT)
        print(f"SoX завершился с кодом {capture.returncode}", file=sys.stderr)
        stop_process(arecord)
        return 1

    npu_wake: NpuWakeDetector | None = None
    if WAKE_BACKEND == "npu":
        try:
            npu_wake = NpuWakeDetector(NPU_WAKE_COMMAND)
        except (OSError, ValueError) as exc:
            stop_process(capture)
            stop_process(arecord)
            print(f"Не удалось запустить NPU wake backend: {exc}", file=sys.stderr)
            return 1
        print(f"Wake backend: NPU runner ({NPU_WAKE_COMMAND})")
    elif WAKE_BACKEND != "asr":
        stop_process(capture)
        stop_process(arecord)
        print(f"Неизвестный KOT_WAKE_BACKEND: {WAKE_BACKEND}", file=sys.stderr)
        return 1

    assert capture.stdout is not None
    initial_music = music.snapshot()
    edge.patch(
        mic_online=True,
        mode="music" if initial_music.playing else "idle",
        heard_text="",
        reply_text="",
        music_playing=initial_music.playing,
        music_available=initial_music.available,
        music_status=initial_music.status,
        track=initial_music.track,
        artist=initial_music.artist,
        volume=initial_music.volume,
    )

    bytes_per_window = WINDOW_SIZE * 2
    wake_window_samples = int(WAKE_WINDOW_SECONDS * SAMPLE_RATE)
    wake_min_samples = int(WAKE_MIN_AUDIO_SECONDS * SAMPLE_RATE)
    command_preroll_samples = int(COMMAND_PREROLL_SECONDS * SAMPLE_RATE)

    rolling = np.empty(0, dtype=np.float32)
    state = "WAIT_WAKE"
    next_wake_decode = time.monotonic()
    command_deadline: float | None = None
    music_was_playing = False

    print()
    print("================================================")
    print("           KOT EDGE VOICE")
    print("================================================")
    print(f"Микрофон:     {MIC_DEVICE}")
    print("Вход:         8 ch / 48000 Hz")
    print("Beam:         CH6")
    print("ASR:          mono / 16000 Hz")
    print(f"Gain:         x{GAIN}")
    print('Wake phrase:  "Эй, Кот"')
    print(f"Command wait: {COMMAND_TIMEOUT_SECONDS:.0f} c")
    print()
    print("[WAIT_WAKE] Жду: Эй, Кот")

    try:
        while True:
            raw = read_exact(capture.stdout, bytes_per_window)
            if raw is None:
                edge.patch(mic_online=False, mode="error", message=MIC_STREAM_STOPPED_TEXT)
                print("Поток микрофона остановился.", file=sys.stderr)
                break

            samples = pcm_to_float(raw)
            now = time.monotonic()

            if state == "WAIT_WAKE":
                rolling = append_rolling(rolling, samples, wake_window_samples)

                npu_event = npu_wake.accept(samples) if npu_wake is not None else None
                run_asr_wake = (
                    npu_wake is None
                    and len(rolling) >= wake_min_samples
                    and now >= next_wake_decode
                )
                if (npu_event is not None and npu_event.detected) or run_asr_wake:
                    if npu_event is not None:
                        score = "" if npu_event.score is None else f" score={npu_event.score:.3f}"
                        normalized = f"эй кот [npu{score}]"
                        found = True
                    else:
                        next_wake_decode = now + WAKE_DECODE_INTERVAL
                        text, _, rtf = recognize(recognizer, rolling)
                        normalized = normalize_text(text)

                        if DEBUG_WAKE_ASR and normalized:
                            print(f"[WAKE-ASR] {normalized} (RTF={rtf:.2f})")

                        found, _, _ = find_wake(normalized)
                    if found:
                        # Silence Chromium/Yandex Music before active ASR. The
                        # previous state is remembered so non-music commands and
                        # timeouts can restore playback automatically.
                        music_was_playing = music.pause_for_wake(MUSIC_WAKE_MUTE_SECONDS)
                        mic_leds.set_listening(True)
                        print()
                        print(f"[WAKE] {normalized}")
                        print("[ACTIVE] Слушаю команду...")
                        print()

                        edge.patch(
                            mode="listening",
                            heard_text=normalized,
                            mic_online=True,
                            music_playing=False,
                        )
                        state = "ACTIVE"
                        command_deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
                        vad = reset_vad(vad)

                        preroll = rolling[-command_preroll_samples:].copy()
                        pos = 0
                        while pos + WINDOW_SIZE <= len(preroll):
                            vad.accept_waveform(preroll[pos : pos + WINDOW_SIZE])
                            pos += WINDOW_SIZE

                        command_done, wake_only = process_vad_segments(
                            vad, recognizer, edge, music, music_was_playing
                        )
                        if command_done:
                            mic_leds.set_listening(False)
                            state = "WAIT_WAKE"
                            vad = reset_vad(vad)
                            rolling = np.empty(0, dtype=np.float32)
                            next_wake_decode = time.monotonic() + WAKE_COOLDOWN_SECONDS
                            music_was_playing = False
                            print("[WAIT_WAKE] Жду: Эй, Кот")
                            print()
                        elif wake_only:
                            command_deadline = (
                                time.monotonic() + COMMAND_TIMEOUT_AFTER_WAKE_ONLY
                            )
                        continue

                continue

            vad.accept_waveform(samples)
            command_done, wake_only = process_vad_segments(
                vad, recognizer, edge, music, music_was_playing
            )

            if wake_only:
                command_deadline = time.monotonic() + COMMAND_TIMEOUT_AFTER_WAKE_ONLY

            if command_done:
                mic_leds.set_listening(False)
                state = "WAIT_WAKE"
                vad = reset_vad(vad)
                rolling = np.empty(0, dtype=np.float32)
                next_wake_decode = time.monotonic() + WAKE_COOLDOWN_SECONDS
                music_was_playing = False
                print("[WAIT_WAKE] Жду: Эй, Кот")
                print()
                continue

            if command_deadline is not None and time.monotonic() >= command_deadline:
                print()
                print("[TIMEOUT] Команда не получена.")
                print("[WAIT_WAKE] Жду: Эй, Кот")
                print()
                music_snapshot = music.resume_after_timeout(music_was_playing)
                mic_leds.set_listening(False)
                edge.patch(
                    mode="music" if music_snapshot.playing else "idle",
                    mic_online=True,
                    music_playing=music_snapshot.playing,
                    music_available=music_snapshot.available,
                    music_status=music_snapshot.status,
                    track=music_snapshot.track,
                    artist=music_snapshot.artist,
                    volume=music_snapshot.volume,
                )
                state = "WAIT_WAKE"
                vad = reset_vad(vad)
                rolling = np.empty(0, dtype=np.float32)
                next_wake_decode = time.monotonic() + WAKE_COOLDOWN_SECONDS
                command_deadline = None
                music_was_playing = False

    except KeyboardInterrupt:
        print()
        print("Остановка...")
    finally:
        mic_leds.set_listening(False)
        if state == "ACTIVE" and music_was_playing:
            music.play()
        stop_process(capture)
        stop_process(arecord)
        if npu_wake is not None:
            npu_wake.close()
        edge.patch(mic_online=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
