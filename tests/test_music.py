from kot.music import PlayerController, parse_music_command


def test_music_next_commands() -> None:
    assert parse_music_command("следующий трек") == "next"
    assert parse_music_command("давай следующий") == "next"


def test_music_previous_commands() -> None:
    assert parse_music_command("предыдущий трек") == "previous"
    assert parse_music_command("верни песню") == "previous"


def test_music_pause_commands() -> None:
    assert parse_music_command("пауза") == "pause"
    assert parse_music_command("останови музыку") == "pause"


def test_music_play_commands() -> None:
    assert parse_music_command("продолжи музыку") == "play"
    assert parse_music_command("включи музыку") == "play"


def test_music_volume_commands() -> None:
    assert parse_music_command("громче") == "volume_up"
    assert parse_music_command("сделай музыку тише") == "volume_down"


def test_now_playing_commands() -> None:
    assert parse_music_command("что сейчас играет") == "now_playing"
    assert parse_music_command("какой трек") == "now_playing"


def test_non_music_command_is_not_captured() -> None:
    assert parse_music_command("включи свет в спальне") is None
    assert parse_music_command("открой погоду") is None


def test_snapshot_combines_playerctl_data() -> None:
    class FakeController(PlayerController):
        def __init__(self) -> None:
            pass

        def status(self) -> str | None:
            return "Playing"

        def metadata(self) -> tuple[str | None, str | None]:
            return "Спокойная ночь", "Кино"

        def volume(self) -> int | None:
            return 37

    snapshot = FakeController().snapshot()
    assert snapshot.available is True
    assert snapshot.playing is True
    assert snapshot.status == "Playing"
    assert snapshot.track == "Спокойная ночь"
    assert snapshot.artist == "Кино"
    assert snapshot.volume == 37


def test_volume_command_changes_volume_and_restores_playback() -> None:
    class FakeController(PlayerController):
        def __init__(self) -> None:
            self.volume_step = 0.05
            self._volume = 40
            self._playing = False

        def status(self) -> str | None:
            return "Playing" if self._playing else "Paused"

        def metadata(self) -> tuple[str | None, str | None]:
            return "Track", "Artist"

        def volume(self) -> int | None:
            return self._volume

        def set_volume(self, percent: int) -> bool:
            self._volume = percent
            return True

        def play(self) -> bool:
            self._playing = True
            return True

    result = FakeController().finish_command("громче", was_playing=True)
    assert result.snapshot.volume == 45
    assert result.snapshot.playing is True
    assert result.reply_text == "Громкость 45%"


def test_now_playing_returns_track_as_reply() -> None:
    class FakeController(PlayerController):
        def __init__(self) -> None:
            self._playing = False

        def status(self) -> str | None:
            return "Paused"

        def metadata(self) -> tuple[str | None, str | None]:
            return "Спокойная ночь", "Кино"

        def volume(self) -> int | None:
            return 37

    result = FakeController().finish_command("что играет", was_playing=False)
    assert result.reply_text == "Кино — Спокойная ночь"
