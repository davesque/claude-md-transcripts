import json
from pathlib import Path

import pytest

from claude_md_transcripts.schema import (
    AssistantLine,
    CompactSummaryLine,
    CustomTitleLine,
    ImageBlock,
    SkippedLine,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserLine,
    parse_line,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_session.jsonl"


def load_fixture() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def parse_all() -> list:
    return [parse_line(obj) for obj in load_fixture()]


def test_fixture_has_expected_line_count():
    assert len(load_fixture()) == 16


def test_skipped_types_are_marked():
    parsed = parse_all()
    skipped_types = {p.original_type for p in parsed if isinstance(p, SkippedLine)}
    expected = {
        "permission-mode",
        "attachment",
        "file-history-snapshot",
        "system",
        "queue-operation",
        "last-prompt",
    }
    assert expected <= skipped_types


def test_user_text_line_is_parsed():
    parsed = parse_all()
    user_text = [
        p
        for p in parsed
        if isinstance(p, UserLine)
        and not p.is_compact_summary
        and any(isinstance(b, TextBlock) for b in p.content_blocks)
    ]
    assert user_text, "expected at least one user line with a text block"
    line = user_text[0]
    assert line.uuid
    assert line.timestamp
    assert line.session_id
    text = next(b for b in line.content_blocks if isinstance(b, TextBlock))
    assert text.text


def test_user_string_content_is_normalized_to_text_block():
    """Old user lines store content as a raw string; we normalize to a TextBlock."""
    parsed = parse_all()
    string_users = [
        p
        for p in parsed
        if isinstance(p, UserLine)
        and len(p.content_blocks) == 1
        and isinstance(p.content_blocks[0], TextBlock)
        and p.original_content_was_string
    ]
    assert string_users, "expected at least one user line with raw-string content"


def test_assistant_text_line_is_parsed():
    parsed = parse_all()
    assistants = [p for p in parsed if isinstance(p, AssistantLine)]
    assert any(any(isinstance(b, TextBlock) for b in a.content_blocks) for a in assistants)


def test_assistant_thinking_line_is_parsed():
    parsed = parse_all()
    assistants = [p for p in parsed if isinstance(p, AssistantLine)]
    thinking = [
        a for a in assistants if any(isinstance(b, ThinkingBlock) for b in a.content_blocks)
    ]
    assert thinking
    block = next(b for b in thinking[0].content_blocks if isinstance(b, ThinkingBlock))
    assert block.thinking is not None


def test_tool_use_line_is_parsed():
    parsed = parse_all()
    tool_uses = [
        p
        for p in parsed
        if isinstance(p, AssistantLine)
        and any(isinstance(b, ToolUseBlock) for b in p.content_blocks)
    ]
    assert tool_uses
    block = next(b for b in tool_uses[0].content_blocks if isinstance(b, ToolUseBlock))
    assert block.id
    assert block.name
    assert isinstance(block.input, dict)


def test_tool_result_line_is_parsed():
    parsed = parse_all()
    tool_results = [
        p
        for p in parsed
        if isinstance(p, UserLine) and any(isinstance(b, ToolResultBlock) for b in p.content_blocks)
    ]
    assert tool_results
    block = next(b for b in tool_results[0].content_blocks if isinstance(b, ToolResultBlock))
    assert block.tool_use_id


def test_image_block_is_recognized():
    parsed = parse_all()
    image_carriers = [
        p
        for p in parsed
        if isinstance(p, UserLine)
        and any(
            isinstance(b, ToolResultBlock)
            and any(isinstance(s, ImageBlock) for s in b.content_blocks)
            for b in p.content_blocks
        )
    ]
    assert image_carriers, "synthesized image fixture line should be parsed"


def test_sidechain_flag_is_preserved():
    parsed = parse_all()
    sidechains = [p for p in parsed if isinstance(p, AssistantLine) and p.is_sidechain]
    assert sidechains


def test_compact_summary_line_has_dedicated_type():
    parsed = parse_all()
    compacts = [p for p in parsed if isinstance(p, CompactSummaryLine)]
    assert len(compacts) == 1
    assert compacts[0].text


def test_custom_title_line_has_dedicated_type():
    parsed = parse_all()
    titles = [p for p in parsed if isinstance(p, CustomTitleLine)]
    assert len(titles) == 1
    assert titles[0].title == "Indexing Claude transcripts into qmd"


def test_unknown_top_level_type_is_skipped_with_reason():
    obj = {"type": "future-feature", "sessionId": "x", "uuid": "y"}
    result = parse_line(obj)
    assert isinstance(result, SkippedLine)
    assert result.original_type == "future-feature"
    assert "unknown" in result.reason.lower()


def test_extra_fields_in_assistant_line_do_not_break_parsing():
    obj = {
        "type": "assistant",
        "uuid": "a",
        "parentUuid": "b",
        "timestamp": "2026-01-01T00:00:00Z",
        "sessionId": "s",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "model": "claude-x",
            "content": [{"type": "text", "text": "hi"}],
            "newField": "future-claude-code-feature",
        },
        "anotherNewField": 42,
    }
    line = parse_line(obj)
    assert isinstance(line, AssistantLine)
    assert line.content_blocks[0].text == "hi"


def test_unknown_content_block_type_emits_passthrough(caplog):
    obj = {
        "type": "assistant",
        "uuid": "u",
        "parentUuid": None,
        "timestamp": "2026-01-01T00:00:00Z",
        "sessionId": "s",
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "model": "claude-x",
            "content": [{"type": "text", "text": "before"}, {"type": "novel-block", "data": "x"}],
        },
    }
    with caplog.at_level("WARNING"):
        line = parse_line(obj)
    assert isinstance(line, AssistantLine)
    # First block is recognized
    assert isinstance(line.content_blocks[0], TextBlock)
    # Unknown blocks are dropped from typed view but warned about
    assert any("novel-block" in rec.message for rec in caplog.records)


@pytest.mark.parametrize("missing_field", ["uuid", "timestamp"])
def test_assistant_missing_required_fields_falls_back_to_skipped(missing_field):
    obj = {
        "type": "assistant",
        "uuid": "u",
        "parentUuid": None,
        "timestamp": "2026-01-01T00:00:00Z",
        "sessionId": "s",
        "isSidechain": False,
        "message": {"role": "assistant", "model": "x", "content": [{"type": "text", "text": "ok"}]},
    }
    obj.pop(missing_field)
    result = parse_line(obj)
    # The reader is lenient: missing required fields means we skip rather than crash.
    assert isinstance(result, SkippedLine)
