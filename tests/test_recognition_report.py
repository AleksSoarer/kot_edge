from __future__ import annotations

import json

from kot.recognition_report import build_report, main, rotated_log_paths


def write_jsonl(path, *records) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_rotations_are_loaded_oldest_first_and_invalid_lines_are_skipped(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    write_jsonl(
        tmp_path / "recognition.jsonl.2",
        {"timestamp": "oldest", "session_id": "a", "event": "session_start"},
    )
    (tmp_path / "recognition.jsonl.1").write_text(
        '{"timestamp":"middle","session_id":"a","event":"asr",'
        '"stage":"wake","decision":"rejected","normalized":"эйко"}\n'
        "broken\n",
        encoding="utf-8",
    )
    write_jsonl(
        path,
        {"timestamp": "newest", "session_id": "a", "event": "session_stop"},
    )
    warnings = []

    report = build_report(path, warn=warnings.append)

    assert rotated_log_paths(path) == [
        tmp_path / "recognition.jsonl.2",
        tmp_path / "recognition.jsonl.1",
        path,
    ]
    assert report["first_timestamp"] == "oldest"
    assert report["last_timestamp"] == "newest"
    assert report["events"] == 3
    assert report["invalid_lines"] == 1
    assert len(warnings) == 1
    assert "recognition.jsonl.1:2" in warnings[0]


def test_report_summarizes_recognition_events(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    write_jsonl(
        path,
        {"session_id": "s1", "event": "session_start"},
        {
            "session_id": "s1",
            "event": "asr",
            "stage": "wake",
            "decision": "rejected",
            "normalized": "эйко",
            "beam_sector": 3,
        },
        {
            "session_id": "s1",
            "event": "asr",
            "stage": "wake",
            "decision": "rejected",
            "normalized": "эйко",
            "beam_sector": 3,
        },
        {
            "session_id": "s1",
            "event": "asr",
            "stage": "wake",
            "decision": "wake",
            "normalized": "эй кот",
            "beam_sector": 4,
        },
        {
            "session_id": "s1",
            "event": "wake_detected",
            "backend": "asr",
            "source_text": "эй кот",
            "beam_sector": 4,
        },
        {"session_id": "s1", "event": "command", "text": "включи музыку"},
        {"session_id": "s2", "event": "session_start"},
        {
            "session_id": "s2",
            "event": "wake_detected",
            "backend": "npu",
            "source_text": "эй кот [npu]",
            "beam_sector": 5,
        },
        {"session_id": "s2", "event": "timeout"},
    )

    report = build_report(path)

    assert report["sessions"] == 2
    assert report["events"] == 9
    assert report["event_counts"] == {
        "asr": 3,
        "command": 1,
        "session_start": 2,
        "timeout": 1,
        "wake_detected": 2,
    }
    assert report["accepted_wakes"] == 2
    assert report["accepted_wake_phrases"] == [
        {"text": "эй кот", "count": 1},
        {"text": "эй кот [npu]", "count": 1},
    ]
    assert report["rejected_wakes"] == 2
    assert report["rejected_wake_candidates"] == [{"text": "эйко", "count": 2}]
    assert report["commands"] == 1
    assert report["command_phrases"] == [{"text": "включи музыку", "count": 1}]
    assert report["timeouts"] == 1
    assert report["beam_sectors"] == [
        {"sector": 3, "count": 2},
        {"sector": 4, "count": 1},
        {"sector": 5, "count": 1},
    ]


def test_report_includes_capture_route_and_latest_hotmap(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    write_jsonl(
        path,
        {
            "session_id": "s1",
            "event": "session_start",
            "wake_channel": 7,
            "selected_channel": 6,
        },
        {
            "session_id": "s1",
            "event": "beam_diagnostic",
            "connected": True,
            "locked": False,
            "frames_total": 42,
            "frames_since_open": 42,
            "last_frame_age": 0.1,
            "last_contrast": 80,
            "candidate_sector": 3,
            "voted_sector": 3,
            "commanded_sector": 3,
        },
    )

    report = build_report(path)

    assert report["capture_routes"] == [
        {"wake_channel": 7, "command_channel": 6, "sessions": 1}
    ]
    assert report["last_beam_diagnostic"]["frames_total"] == 42


def test_top_limits_ranked_candidates(tmp_path) -> None:
    path = tmp_path / "recognition.jsonl"
    write_jsonl(
        path,
        {
            "session_id": "s1",
            "event": "asr",
            "stage": "wake",
            "decision": "rejected",
            "normalized": "редко",
        },
        *(
            {
                "session_id": "s1",
                "event": "asr",
                "stage": "wake",
                "decision": "rejected",
                "normalized": "часто",
            }
            for _ in range(2)
        ),
    )

    report = build_report(path, top=1)

    assert report["rejected_wake_candidates"] == [{"text": "часто", "count": 2}]


def test_json_cli_keeps_warning_on_stderr(tmp_path, capsys) -> None:
    path = tmp_path / "recognition.jsonl"
    path.write_text(
        '{"session_id":"s1","event":"timeout"}\nnot-json\n', encoding="utf-8"
    )

    assert main([str(path), "--json"]) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["timeouts"] == 1
    assert report["invalid_lines"] == 1
    assert "Предупреждение" in captured.err


def test_cli_returns_two_for_missing_log(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.jsonl"

    assert main([str(missing)]) == 2

    assert "Журнал не найден" in capsys.readouterr().err


def test_cli_returns_two_when_all_log_files_are_unreadable(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:
    path = tmp_path / "recognition.jsonl"
    write_jsonl(path, {"event": "session_start"})
    original_open = type(path).open

    def denied_open(self, *args, **kwargs):
        if self == path:
            raise PermissionError("denied")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", denied_open)

    assert main([str(path)]) == 2
    assert "Не удалось прочитать журнал" in capsys.readouterr().err
