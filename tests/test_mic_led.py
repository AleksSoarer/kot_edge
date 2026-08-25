from unittest.mock import patch

from kot.mic_led import MicArrayLeds


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
