import json

from kot.recognition_log import RecognitionLogger


def test_recognition_log_writes_jsonl(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    log = RecognitionLogger(path)
    log.event(
        "asr",
        stage="wake",
        text="Эй, Кот",
        normalized="эй кот",
        wake_detected=True,
    )
    log.close()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["event"] == "asr"
    assert records[0]["schema_version"] == 1
    assert records[0]["seq"] == 1
    assert records[0]["session_id"]
    assert records[0]["stage"] == "wake"
    assert records[0]["normalized"] == "эй кот"
    assert records[0]["wake_detected"] is True
    assert records[0]["timestamp"].endswith("+00:00")


def test_overlapping_results_are_not_deduplicated(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    log = RecognitionLogger(path)
    log.event("asr", stage="wake", normalized="эй кот", stream_end_sample=48000)
    log.event("asr", stage="wake", normalized="эй кот", stream_end_sample=60800)
    log.close()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["seq"] for record in records] == [1, 2]
    assert [record["stream_end_sample"] for record in records] == [48000, 60800]


def test_accepted_mode_filters_rejected_asr_candidates(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    log = RecognitionLogger(path, mode="accepted")
    log.event("asr", normalized="посторонняя речь", accepted=False)
    log.event("asr", normalized="эй кот", accepted=True)
    log.close()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["normalized"] for record in records] == ["эй кот"]


def test_disabled_recognition_log_does_not_write(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    log = RecognitionLogger(None)
    log.event("asr", text="ничего")
    log.close()
    assert not path.exists()


def test_log_appends_across_sessions(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    first = RecognitionLogger(path)
    first.event("session_start")
    first.close()
    second = RecognitionLogger(path)
    second.event("session_start")
    second.close()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["session_id"] != records[1]["session_id"]


def test_invalid_mode_disables_log_without_raising(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    log = RecognitionLogger(path, mode="typo")
    log.event("asr", normalized="эй кот")
    assert not path.exists()
