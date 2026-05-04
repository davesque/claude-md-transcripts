import json
from pathlib import Path

from claude_md_transcripts.reader import (
    DEFAULT_MAX_BYTES,
    ReaderResult,
    read_session,
)
from claude_md_transcripts.schema import (
    AssistantLine,
    CustomTitleLine,
    SkippedLine,
    UserLine,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_session.jsonl"


def test_read_session_returns_records_with_line_numbers():
    result = read_session(FIXTURE)
    assert isinstance(result, ReaderResult)
    assert result.path == FIXTURE
    assert result.size_bytes > 0
    assert len(result.records) == 16
    for rec in result.records:
        assert rec.line_number >= 1


def test_read_session_classifies_lines():
    result = read_session(FIXTURE)
    types_seen = {type(r.parsed).__name__ for r in result.records}
    assert {
        "UserLine",
        "AssistantLine",
        "CompactSummaryLine",
        "CustomTitleLine",
        "SkippedLine",
    } <= types_seen


def test_read_session_records_in_order():
    result = read_session(FIXTURE)
    line_numbers = [r.line_number for r in result.records]
    assert line_numbers == sorted(line_numbers)


def test_read_session_skipped_count_matches():
    result = read_session(FIXTURE)
    skipped = sum(1 for r in result.records if isinstance(r.parsed, SkippedLine))
    assert result.skipped_count == skipped
    assert skipped >= 5  # auxiliary types in the fixture


def test_read_session_returns_kept_filter():
    result = read_session(FIXTURE)
    kept = list(result.iter_kept())
    assert all(not isinstance(r.parsed, SkippedLine) for r in kept)
    assert kept, "should have at least one kept record"


def test_max_bytes_skips_oversized_file(tmp_path):
    big = tmp_path / "huge.jsonl"
    big.write_bytes(b"x" * 1024)
    result = read_session(big, max_bytes=512)
    assert result.skipped_for_size is True
    assert result.records == []


def test_max_bytes_default_is_50mb():
    assert DEFAULT_MAX_BYTES == 50 * 1024 * 1024


def test_invalid_json_line_is_recorded_but_does_not_abort(tmp_path):
    p = tmp_path / "broken.jsonl"
    valid = {
        "type": "user",
        "uuid": "u",
        "parentUuid": None,
        "timestamp": "2026-01-01T00:00:00Z",
        "sessionId": "s",
        "isSidechain": False,
        "message": {"role": "user", "content": "hi"},
    }
    with p.open("w") as f:
        f.write(json.dumps(valid) + "\n")
        f.write("{not valid json\n")
        f.write(json.dumps(valid) + "\n")
    result = read_session(p)
    parsed_user_lines = [r for r in result.records if isinstance(r.parsed, UserLine)]
    assert len(parsed_user_lines) == 2
    assert result.parse_errors == 1


def test_session_id_inferred_from_records():
    result = read_session(FIXTURE)
    sid = result.session_id
    # Sample fixture's session id (not redacted)
    assert sid == "af6ff891-b945-426b-b678-18798e66b843"


def test_custom_title_extracted_when_present():
    result = read_session(FIXTURE)
    titles = [r.parsed for r in result.records if isinstance(r.parsed, CustomTitleLine)]
    assert result.custom_title == titles[0].title


def test_assistant_records_are_typed():
    result = read_session(FIXTURE)
    a = next(r.parsed for r in result.records if isinstance(r.parsed, AssistantLine))
    assert a.uuid
    assert a.session_id
