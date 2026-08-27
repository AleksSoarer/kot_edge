from kot.phrases import find_wake, is_probable_wake_only, strip_wake_from_command


def test_find_wake_requires_word_boundary() -> None:
    assert find_wake("эй кот")[0]
    assert find_wake("эйкот")[0]
    assert find_wake("шум эй кот включи музыку")[2] == "включи музыку"
    assert find_wake("эйкот включи музыку")[2] == "включи музыку"
    assert not find_wake("бэй кот")[0]
    assert not find_wake("бэйкот")[0]


def test_strip_joined_wake_from_active_command() -> None:
    assert strip_wake_from_command("эйкот пауза") == "пауза"
    assert strip_wake_from_command("эйкод") == ""


def test_short_distorted_wake_is_not_a_command() -> None:
    assert is_probable_wake_only("рим кот")
    assert is_probable_wake_only("день кот")
    assert not is_probable_wake_only("включи музыку")
