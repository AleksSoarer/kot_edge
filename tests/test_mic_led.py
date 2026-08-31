import math
from unittest.mock import call, patch

from kot.mic_led import HOTMAP_HEADER, HotmapParser, MicArrayLeds, hotmap_sector


def hotmap_with_peak(x: int, y: int) -> bytes:
    payload = bytearray(256)
    payload[y * 16 + x] = 255
    return bytes(payload)


def test_hotmap_parser_accepts_split_frame() -> None:
    parser = HotmapParser()
    frame = HOTMAP_HEADER + hotmap_with_peak(8, 0)
    assert parser.feed(b"noise" + frame[:100]) == []
    assert parser.feed(frame[100:]) == [hotmap_with_peak(8, 0)]


def test_hotmap_cardinal_sectors() -> None:
    assert hotmap_sector(hotmap_with_peak(8, 0)) == 0
    assert hotmap_sector(hotmap_with_peak(15, 8)) == 3
    assert hotmap_sector(hotmap_with_peak(8, 15)) == 6
    assert hotmap_sector(hotmap_with_peak(0, 8)) == 9


def test_hotmap_orientation_can_be_calibrated() -> None:
    top = hotmap_with_peak(8, 0)
    assert hotmap_sector(top, offset=2) == 2
    assert hotmap_sector(hotmap_with_peak(15, 8), clockwise=False) == 9


def test_hotmap_sector_uses_half_up_rounding_at_boundary() -> None:
    with patch("kot.mic_led.math.atan2", return_value=math.pi / 12):
        assert hotmap_sector(hotmap_with_peak(8, 0)) == 1


def test_auto_beam_sends_sector_after_stable_frames() -> None:
    controller = MicArrayLeds("/dev/ttyACM0", auto_beam=True, stable_frames=2)
    frame = HOTMAP_HEADER + hotmap_with_peak(15, 8)
    with (
        patch("kot.mic_led.os.open", return_value=9),
        patch("kot.mic_led.os.read", side_effect=[frame + frame, BlockingIOError]),
        patch("kot.mic_led.os.write") as write,
    ):
        assert controller.poll() == 3
    write.assert_called_once_with(9, b"3")


def test_auto_beam_uses_majority_and_tolerates_one_outlier() -> None:
    controller = MicArrayLeds("/dev/ttyACM0", auto_beam=True, stable_frames=3)
    right = HOTMAP_HEADER + hotmap_with_peak(15, 8)
    bottom = HOTMAP_HEADER + hotmap_with_peak(8, 15)
    with (
        patch("kot.mic_led.os.open", return_value=9),
        patch("kot.mic_led.os.read", side_effect=[right + bottom + right, BlockingIOError]),
        patch("kot.mic_led.os.write") as write,
    ):
        assert controller.poll() == 3
    write.assert_called_once_with(9, b"3")


def test_invalid_hotmap_is_counted_in_majority_window() -> None:
    controller = MicArrayLeds("/dev/ttyACM0", auto_beam=True, stable_frames=3)
    right = HOTMAP_HEADER + hotmap_with_peak(15, 8)
    flat = HOTMAP_HEADER + bytes(256)
    with (
        patch("kot.mic_led.os.open", return_value=9),
        patch("kot.mic_led.os.read", side_effect=[right + flat + right, BlockingIOError]),
        patch("kot.mic_led.os.write") as write,
    ):
        assert controller.poll() == 3
    write.assert_called_once_with(9, b"3")


def test_auto_beam_resends_sector_after_device_reopen() -> None:
    controller = MicArrayLeds("/dev/ttyACM0", auto_beam=True, stable_frames=2)
    frame = HOTMAP_HEADER + hotmap_with_peak(15, 8)
    with (
        patch("kot.mic_led.os.open", side_effect=[9, 10]),
        patch(
            "kot.mic_led.os.read",
            side_effect=[
                frame + frame,
                BlockingIOError,
                OSError("disconnected"),
                frame + frame,
                BlockingIOError,
            ],
        ),
        patch("kot.mic_led.os.write") as write,
        patch("kot.mic_led.os.close") as close,
    ):
        assert controller.poll() == 3
        assert controller.poll() is None
        assert controller.poll() == 3
    assert write.call_args_list == [call(9, b"3"), call(10, b"3")]
    close.assert_called_once_with(9)


def test_hotmap_diagnostics_report_actual_stream_state() -> None:
    controller = MicArrayLeds("/dev/ttyACM0", auto_beam=True, stable_frames=2)
    frame = HOTMAP_HEADER + hotmap_with_peak(15, 8)
    with (
        patch("kot.mic_led.time.monotonic", return_value=10.0),
        patch("kot.mic_led.os.open", return_value=9),
        patch("kot.mic_led.os.read", side_effect=[frame + frame, BlockingIOError]),
        patch("kot.mic_led.os.write"),
    ):
        controller.poll()

    diagnostics = controller.diagnostics(now=12.5)
    assert diagnostics == {
        "auto_beam": True,
        "connected": True,
        "locked": False,
        "frames_total": 2,
        "frames_since_open": 2,
        "last_frame_age": 2.5,
        "last_contrast": 255,
        "candidate_sector": 3,
        "voted_sector": 3,
        "commanded_sector": 3,
    }
    assert "frames=2" in controller.diagnostic_summary(now=12.5)
    assert "candidate=3" in controller.diagnostic_summary(now=12.5)


def test_auto_beam_warns_once_when_no_hotmap_frames(capsys) -> None:
    controller = MicArrayLeds(
        "/dev/ttyACM0",
        auto_beam=True,
        no_frame_warning_seconds=5.0,
    )
    with (
        patch("kot.mic_led.time.monotonic", side_effect=[10.0, 16.0, 17.0]),
        patch("kot.mic_led.os.open", return_value=9),
        patch("kot.mic_led.os.read", side_effect=BlockingIOError),
    ):
        controller.poll()
        controller.poll()
        controller.poll()
    assert capsys.readouterr().out.count("кадры hotmap") == 1


def test_leds_send_red_mode_when_listening() -> None:
    leds = MicArrayLeds("/dev/ttyACM0")
    with (
        patch("kot.mic_led.os.open", return_value=7),
        patch("kot.mic_led.os.write") as write,
        patch("kot.mic_led.os.close") as close,
    ):
        leds.set_listening(True)
    write.assert_called_once_with(7, b"e")
    close.assert_called_once_with(7)


def test_leds_restore_sound_direction_mode_when_finished() -> None:
    leds = MicArrayLeds("/dev/ttyACM0")
    with (
        patch("kot.mic_led.os.open", return_value=8),
        patch("kot.mic_led.os.write") as write,
        patch("kot.mic_led.os.close"),
    ):
        leds.set_listening(False)
    write.assert_called_once_with(8, b"E")


def test_disabled_leds_do_not_open_device() -> None:
    leds = MicArrayLeds("/dev/ttyACM0", enabled=False)
    with patch("kot.mic_led.os.open") as open_device:
        leds.set_listening(True)
    open_device.assert_not_called()
