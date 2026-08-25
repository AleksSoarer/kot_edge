from kot.wake import WakeEvent, parse_runner_event


def test_parse_runner_event() -> None:
    assert parse_runner_event('{"wake": true, "score": 0.91}') == WakeEvent(True, 0.91)


def test_parse_runner_event_ignores_logs() -> None:
    assert parse_runner_event("model loaded") is None
    assert parse_runner_event('{"score": 0.5}') is None
