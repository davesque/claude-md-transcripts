from pathlib import Path

import pytest

from claude_md_transcripts.reader import read_session
from claude_md_transcripts.render import RenderConfig, render_session

FIXTURE = Path(__file__).parent / "fixtures" / "sample_session.jsonl"


@pytest.fixture
def rendered() -> str:
    result = read_session(FIXTURE)
    return render_session(result)


@pytest.fixture
def rendered_with_thinking() -> str:
    result = read_session(FIXTURE)
    return render_session(result, RenderConfig(include_thinking=True))


def test_frontmatter_present(rendered: str):
    assert rendered.startswith("---\n")
    head, _, _ = rendered.partition("\n---\n")
    assert "session_id:" in head
    assert "source_path:" in head
    assert "message_count:" in head


def test_frontmatter_includes_custom_title(rendered: str):
    assert "title: Indexing Claude transcripts into qmd" in rendered


def test_user_text_section_is_rendered(rendered: str):
    assert "## User" in rendered


def test_assistant_text_section_is_rendered(rendered: str):
    assert "## Assistant" in rendered


def test_thinking_omitted_by_default(rendered: str):
    # The redacted thinking block from our fixture would say "[redacted thinking]"
    # if it were rendered; verify it's not there.
    assert "[redacted thinking]" not in rendered
    assert "### Thinking" not in rendered


def test_thinking_included_when_enabled(rendered_with_thinking: str):
    assert "### Thinking" in rendered_with_thinking


def test_tool_use_renders_command_and_input(rendered: str):
    # Fixture has a Grep tool_use with input pattern "jsonl|\\.json"
    assert "### Tool call: Grep" in rendered
    assert "```json" in rendered
    assert '"pattern"' in rendered


def test_tool_result_renders_as_pointer(rendered: str):
    # Tool results should appear as a single-line/blockquote pointer
    # rather than dumping their content.
    assert "tool_result" in rendered.lower()
    # The original 200+-char tool_result content from the fixture should not be inlined verbatim
    # (we render a pointer, not the body)
    assert "Found 3 files\nsrc/mcp/server.ts" not in rendered


def test_tool_result_includes_originating_tool_name_when_known(rendered: str):
    # The Grep tool's tool_use_id is referenced by a tool_result; renderer should resolve.
    assert "tool_result for Grep" in rendered


def test_image_block_is_replaced_with_placeholder(rendered: str):
    # Synthesized image fixture line; base64 must not appear, placeholder must.
    assert "iVBORw0KGgo" not in rendered
    assert "image attachment" in rendered.lower()


def test_compact_summary_has_dedicated_header(rendered: str):
    assert "## Compaction summary" in rendered


def test_sidechain_marked_as_subagent(rendered: str):
    assert "[subagent]" in rendered.lower()
    assert "Subagent reporting findings" in rendered


def test_renders_old_string_user_content(rendered: str):
    # User-line with raw-string content should render as ordinary text under ## User
    assert "hey claude!" in rendered


def test_long_tool_input_is_truncated():
    """A pathologically large tool_use input should be truncated in render."""
    from claude_md_transcripts.render import _render_tool_use
    from claude_md_transcripts.schema import ToolUseBlock

    block = ToolUseBlock(id="t1", name="Write", input={"content": "x" * 10000})
    out = _render_tool_use(block, max_input_chars=500)
    assert len(out) < 2000
    assert "truncated" in out.lower()


def test_pointer_uses_source_path_and_uuid(rendered: str):
    # Pointer should embed the originating jsonl path and the line's uuid
    # so a query agent can dig deeper.
    assert ".jsonl" in rendered
    # at least one short uuid appears in a pointer
    import re

    assert re.search(r"uuid=[0-9a-f]{8}", rendered) is not None
