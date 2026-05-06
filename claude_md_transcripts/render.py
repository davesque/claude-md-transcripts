"""
Render parsed session records into markdown.

The output is designed for downstream indexing or full-text search:
text-bearing content is preserved, tool I/O is replaced with one-line
pointers back into the source JSONL, and images are dropped in favor
of a placeholder. Frontmatter carries session metadata so downstream
tools can filter without re-parsing the original file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .reader import ReaderResult, SessionRecord
from .schema import (
    AssistantLine,
    CompactSummaryLine,
    CustomTitleLine,
    ImageBlock,
    SkippedLine,
    TextBlock,
    ThinkingBlock,
    ToolReferenceBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserLine,
)


@dataclass(frozen=True)
class RenderConfig:
    """
    Toggles for the markdown renderer.

    Attributes
    ----------
    include_thinking
        If True, render assistant ``thinking`` blocks under their own subhead.
        Off by default, since extended thinking is verbose and often noisy
        for cross-session retrieval.
    max_tool_input_chars
        Tool ``input`` payloads larger than this are truncated in the rendered
        markdown with a placeholder. The full content remains in the source
        JSONL for any agent that wants to dig deeper.
    """

    include_thinking: bool = False
    max_tool_input_chars: int = 2000


def render_session(result: ReaderResult, config: RenderConfig | None = None) -> str:
    """
    Render a parsed session into markdown.

    Parameters
    ----------
    result
        Output of :func:`claude_md_transcripts.reader.read_session`.
    config
        Optional render configuration.

    Returns
    -------
    str
        Markdown text, frontmatter-prefixed.
    """
    cfg = config or RenderConfig()
    tool_names = _build_tool_name_index(result.records)
    parts: list[str] = [_render_frontmatter(result)]
    for rec in result.records:
        block = _render_record(rec, result.path, tool_names, cfg)
        if block:
            parts.append(block)
    return "\n\n".join(parts).rstrip() + "\n"


def _build_tool_name_index(records: list[SessionRecord]) -> dict[str, str]:
    """
    Walk records and collect a map from ``tool_use.id`` to ``tool_use.name``.
    """
    out: dict[str, str] = {}
    for rec in records:
        line = rec.parsed
        if isinstance(line, AssistantLine):
            for block in line.content_blocks:
                if isinstance(block, ToolUseBlock):
                    out[block.id] = block.name
    return out


def _render_frontmatter(result: ReaderResult) -> str:
    """
    Build the YAML-ish frontmatter block for the rendered file.
    """
    kept = list(result.iter_kept())
    timestamps = [
        getattr(r.parsed, "timestamp", None) for r in kept if getattr(r.parsed, "timestamp", None)
    ]
    start = timestamps[0] if timestamps else ""
    end = timestamps[-1] if timestamps else ""
    fields = {
        "session_id": result.session_id or "",
        "source_path": str(result.path),
        "message_count": len(kept),
        "start_time": start,
        "end_time": end,
    }
    if result.custom_title:
        fields["title"] = result.custom_title
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _render_record(
    rec: SessionRecord,
    source_path: Path,
    tool_names: dict[str, str],
    cfg: RenderConfig,
) -> str:
    """
    Render a single record. Returns an empty string for entries we skip.
    """
    line = rec.parsed
    if isinstance(line, SkippedLine):
        return ""
    if isinstance(line, CustomTitleLine):
        return ""  # surfaced via frontmatter
    if isinstance(line, CompactSummaryLine):
        return f"## Compaction summary\n\n{line.text.strip()}"
    if isinstance(line, UserLine):
        return _render_user(rec, line, source_path, tool_names)
    if isinstance(line, AssistantLine):
        return _render_assistant(rec, line, source_path, cfg)
    return ""


def _role_header(role: str, line: UserLine | AssistantLine) -> str:
    """
    Build a section header annotated with sidechain origin and timestamp.
    """
    suffix_bits: list[str] = []
    if line.is_sidechain:
        suffix_bits.append("[subagent]")
    if line.timestamp:
        suffix_bits.append(line.timestamp)
    suffix = f" — {' '.join(suffix_bits)}" if suffix_bits else ""
    return f"## {role}{suffix}"


def _render_user(
    rec: SessionRecord, line: UserLine, source_path: Path, tool_names: dict[str, str]
) -> str:
    """
    Render one user line.

    User lines come in two flavors: prose (real prompts and old-format
    string messages) and tool_results. Prose renders under a ``## User``
    header; tool_results render as a one-line pointer back to the source.
    """
    text_parts: list[str] = []
    pointer_parts: list[str] = []
    for block in line.content_blocks:
        if isinstance(block, TextBlock):
            if block.text.strip():
                text_parts.append(block.text.strip())
        elif isinstance(block, ToolResultBlock):
            pointer_parts.append(
                _render_tool_result(block, line, source_path, rec.line_number, tool_names)
            )
    sections: list[str] = []
    if text_parts:
        sections.append(_role_header("User", line) + "\n\n" + "\n\n".join(text_parts))
    if pointer_parts:
        sections.append("\n\n".join(pointer_parts))
    return "\n\n".join(sections)


def _render_assistant(
    rec: SessionRecord,
    line: AssistantLine,
    source_path: Path,
    cfg: RenderConfig,
) -> str:
    """
    Render one assistant line.

    The role header always appears once; thinking, text, and tool_use blocks
    are emitted in source order so a reader can follow the assistant's flow.
    """
    body_parts: list[str] = []
    for block in line.content_blocks:
        if isinstance(block, TextBlock):
            if block.text.strip():
                body_parts.append(block.text.strip())
        elif isinstance(block, ThinkingBlock):
            if cfg.include_thinking and block.thinking.strip():
                body_parts.append("### Thinking\n\n" + block.thinking.strip())
        elif isinstance(block, ToolUseBlock):
            body_parts.append(_render_tool_use(block, cfg.max_tool_input_chars))
    if not body_parts:
        return ""
    return _role_header("Assistant", line) + "\n\n" + "\n\n".join(body_parts)


def _render_tool_use(block: ToolUseBlock, max_input_chars: int) -> str:
    """
    Render a tool_use block as a fenced JSON code block.

    Long inputs (e.g. file content for Write) are truncated; the full
    content remains in the source JSONL.
    """
    encoded = json.dumps(block.input, indent=2, ensure_ascii=False)
    if len(encoded) > max_input_chars:
        encoded = encoded[:max_input_chars] + "\n... [truncated; see source JSONL]"
    return f"### Tool call: {block.name}\n\n```json\n{encoded}\n```"


def _render_tool_result(
    block: ToolResultBlock,
    line: UserLine,
    source_path: Path,
    line_number: int,
    tool_names: dict[str, str],
) -> str:
    """
    Render a tool_result as a one-line pointer plus optional structured summary.

    Image sub-blocks are flattened to a placeholder. Text sub-blocks contribute
    a short snippet so the rendered file can still surface keywords during
    full-text search, but the bulk of the content is left in the source JSONL.
    """
    tool_name = tool_names.get(block.tool_use_id, "unknown-tool")
    short_uuid = (line.uuid or "")[:8]
    pointer = f"> tool_result for {tool_name} (uuid={short_uuid}, see {source_path}#L{line_number})"
    extras: list[str] = []
    if block.is_error:
        extras.append("> ⚠ tool reported an error")
    summary = _summarize_tool_use_result(line.tool_use_result)
    if summary:
        extras.append(f"> {summary}")
    if any(isinstance(s, ImageBlock) for s in block.content_blocks):
        extras.append(f"> image attachment (see {source_path}#L{line_number})")
    referenced_tools = [
        s.tool_name
        for s in block.content_blocks
        if isinstance(s, ToolReferenceBlock) and s.tool_name
    ]
    if referenced_tools:
        extras.append(f"> referenced tools: {', '.join(referenced_tools)}")
    return "\n".join([pointer, *extras])


def _summarize_tool_use_result(tur: Any) -> str:
    """
    Build a short, grep-friendly summary from the structured ``toolUseResult``.
    """
    if isinstance(tur, dict):
        bits: list[str] = []
        for key in ("mode", "numFiles", "filenames", "filePath", "stdout", "stderr"):
            if key not in tur:
                continue
            v = tur[key]
            if key == "filenames" and isinstance(v, list):
                bits.append(f"filenames={len(v)}")
            elif key in ("stdout", "stderr") and isinstance(v, str):
                if v.strip():
                    snippet = v.strip().splitlines()[0][:80]
                    bits.append(f"{key}={snippet!r}")
            else:
                rendered = repr(v)
                if len(rendered) > 80:
                    rendered = rendered[:80] + "..."
                bits.append(f"{key}={rendered}")
        return ", ".join(bits)
    return ""
