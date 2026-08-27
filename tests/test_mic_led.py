from unittest.mock import patch

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
