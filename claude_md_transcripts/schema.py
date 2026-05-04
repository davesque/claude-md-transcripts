"""
Pydantic models for the Claude Code session JSONL format.

The on-disk schema is not officially documented; these models are derived from
inspection of real session files plus community references. The reader is
deliberately lenient: unknown top-level types and unknown content blocks are
reported via `SkippedLine` or warning logs, not raised, so a converter run
survives Claude Code version drift and reports drift instead of crashing.

Recognized top-level `type` values fall into three groups:

- Conversation lines we render: ``user``, ``assistant``.
- Conversation lines with dedicated handling: a ``user`` line carrying
  ``isCompactSummary`` becomes a :class:`CompactSummaryLine`; a
  ``custom-title`` line becomes a :class:`CustomTitleLine`.
- Auxiliary lines we drop: ``permission-mode``, ``attachment``,
  ``file-history-snapshot``, ``system``, ``queue-operation``, ``last-prompt``,
  ``worktree-state``. These become :class:`SkippedLine` entries with a reason.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

SKIP_TYPES: frozenset[str] = frozenset(
    {
        "permission-mode",
        "attachment",
        "file-history-snapshot",
        "system",
        "queue-operation",
        "last-prompt",
        "worktree-state",
    }
)


class _Lenient(BaseModel):
    """
    Base model that ignores unknown fields.

    Claude Code adds fields between versions; we want forward compatibility
    rather than strict rejection.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class TextBlock(_Lenient):
    """
    A plain text content block.
    """

    type: Literal["text"] = "text"
    text: str = ""


class ThinkingBlock(_Lenient):
    """
    Extended-thinking content from the assistant.
    """

    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    signature: str | None = None


class ToolUseBlock(_Lenient):
    """
    Assistant invocation of a tool.
    """

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ImageBlock(_Lenient):
    """
    An image attachment.

    We never render the actual image. The renderer drops these in favor of a
    placeholder pointing back to the source JSONL.
    """

    type: Literal["image"] = "image"
    source: dict[str, Any] | None = None


class ToolReferenceBlock(_Lenient):
    """
    A reference to a tool by name, emitted by some tool_result payloads.

    These are metadata-only and carry no content the renderer cares about.
    """

    type: Literal["tool_reference"] = "tool_reference"
    tool_name: str = ""


# A tool_result's `content` field can be a string or a list of sub-blocks.
# Real-world payloads include text, image, and tool_reference markers.
ToolResultSubBlock = Union[TextBlock, ImageBlock, ToolReferenceBlock]


class ToolResultBlock(_Lenient):
    """
    Tool execution result attached to a user line.
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    is_error: bool = Field(default=False, alias="is_error")
    content_blocks: list[ToolResultSubBlock] = Field(default_factory=list)
    raw_string_content: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ToolResultBlock:
        """
        Build a :class:`ToolResultBlock` from the raw JSON dict.

        Parameters
        ----------
        raw
            The original tool_result block, with ``content`` either a string or
            a list of sub-blocks.

        Returns
        -------
        ToolResultBlock
        """
        content = raw.get("content")
        sub_blocks: list[ToolResultSubBlock] = []
        raw_string: str | None = None
        if isinstance(content, str):
            raw_string = content
        elif isinstance(content, list):
            for sub in content:
                if not isinstance(sub, dict):
                    continue
                stype = sub.get("type")
                if stype == "text":
                    sub_blocks.append(TextBlock.model_validate(sub))
                elif stype == "image":
                    sub_blocks.append(ImageBlock.model_validate(sub))
                elif stype == "tool_reference":
                    sub_blocks.append(ToolReferenceBlock.model_validate(sub))
                else:
                    logger.warning(
                        "tool_result sub-block type %r not recognized; dropping",
                        stype,
                    )
        return cls(
            tool_use_id=raw.get("tool_use_id", ""),
            is_error=bool(raw.get("is_error", False)),
            content_blocks=sub_blocks,
            raw_string_content=raw_string,
        )


AssistantContentBlock = Union[TextBlock, ThinkingBlock, ToolUseBlock]
UserContentBlock = Union[TextBlock, ToolResultBlock]


class _LineBase(_Lenient):
    """
    Common metadata fields present on most JSONL lines.
    """

    uuid: str
    parent_uuid: str | None = Field(default=None, alias="parentUuid")
    timestamp: str
    session_id: str = Field(alias="sessionId")
    is_sidechain: bool = Field(default=False, alias="isSidechain")
    cwd: str | None = None
    git_branch: str | None = Field(default=None, alias="gitBranch")
    version: str | None = None


class UserLine(_LineBase):
    """
    A user-role line. May carry plain text (a real prompt) or tool_result blocks.

    Lines with ``isCompactSummary`` are routed to :class:`CompactSummaryLine`
    by :func:`parse_line`, so this class always represents normal user content.
    """

    role: Literal["user"] = "user"
    content_blocks: list[UserContentBlock] = Field(default_factory=list)
    original_content_was_string: bool = False
    tool_use_result: Any = Field(default=None, alias="toolUseResult")
    source_tool_assistant_uuid: str | None = Field(default=None, alias="sourceToolAssistantUUID")
    is_compact_summary: bool = Field(default=False, alias="isCompactSummary")


class AssistantLine(_LineBase):
    """
    An assistant-role line.
    """

    role: Literal["assistant"] = "assistant"
    model: str | None = None
    content_blocks: list[AssistantContentBlock] = Field(default_factory=list)


class CompactSummaryLine(_LineBase):
    """
    A compaction summary auto-generated by Claude Code.

    These represent rolled-up history when the engine compacted older turns,
    and are gold for retrieval, so they get a dedicated render path.
    """

    role: Literal["user"] = "user"
    text: str
    compact_metadata: dict[str, Any] | None = Field(default=None, alias="compactMetadata")


class CustomTitleLine(BaseModel):
    """
    A session-level title line emitted by Claude Code.
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["custom-title"] = "custom-title"
    title: str = Field(alias="customTitle")
    session_id: str = Field(alias="sessionId")


class SkippedLine(BaseModel):
    """
    Placeholder for a line we deliberately ignore.

    Carries the original type and a short reason for diagnostics.
    """

    model_config = ConfigDict(extra="ignore")

    original_type: str
    reason: str


ParsedLine = Union[
    UserLine,
    AssistantLine,
    CompactSummaryLine,
    CustomTitleLine,
    SkippedLine,
]


def _parse_assistant_blocks(content: Any) -> list[AssistantContentBlock]:
    """
    Convert a raw assistant ``message.content`` value into typed blocks.

    Unknown block types are warned about and dropped.
    """
    blocks: list[AssistantContentBlock] = []
    if not isinstance(content, list):
        return blocks
    for raw in content:
        if not isinstance(raw, dict):
            continue
        btype = raw.get("type")
        if btype == "text":
            blocks.append(TextBlock.model_validate(raw))
        elif btype == "thinking":
            blocks.append(ThinkingBlock.model_validate(raw))
        elif btype == "tool_use":
            blocks.append(ToolUseBlock.model_validate(raw))
        else:
            logger.warning("unknown assistant content block type %r; dropping", btype)
    return blocks


def _parse_user_blocks(content: Any) -> tuple[list[UserContentBlock], bool]:
    """
    Convert a raw user ``message.content`` value into typed blocks.

    Returns
    -------
    blocks
        The typed content blocks.
    was_string
        True if the original content was a raw string (older format).
    """
    if isinstance(content, str):
        return [TextBlock(text=content)], True
    blocks: list[UserContentBlock] = []
    if not isinstance(content, list):
        return blocks, False
    for raw in content:
        if not isinstance(raw, dict):
            continue
        btype = raw.get("type")
        if btype == "text":
            blocks.append(TextBlock.model_validate(raw))
        elif btype == "tool_result":
            blocks.append(ToolResultBlock.from_raw(raw))
        else:
            logger.warning("unknown user content block type %r; dropping", btype)
    return blocks, False


def _extract_compact_text(content: Any) -> str:
    """
    Pull the summary text out of a compact-summary user line.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def parse_line(obj: dict[str, Any]) -> ParsedLine:
    """
    Parse a single JSONL line dict into a typed model.

    Parameters
    ----------
    obj
        The decoded JSON object for one line.

    Returns
    -------
    ParsedLine
        A typed line model, or a :class:`SkippedLine` carrying the reason.
    """
    t = obj.get("type")
    if t in SKIP_TYPES:
        return SkippedLine(original_type=str(t), reason=f"auxiliary type {t!r}")
    if t == "custom-title":
        try:
            return CustomTitleLine.model_validate(obj)
        except ValidationError as e:
            logger.warning("failed to parse custom-title line: %s", e)
            return SkippedLine(original_type="custom-title", reason="invalid custom-title shape")

    if t == "user":
        msg = obj.get("message") or {}
        if not isinstance(msg, dict):
            return SkippedLine(original_type="user", reason="missing message")
        if obj.get("isCompactSummary"):
            text = _extract_compact_text(msg.get("content"))
            try:
                return CompactSummaryLine.model_validate({**obj, "text": text})
            except ValidationError as e:
                logger.warning("failed to parse compact summary: %s", e)
                return SkippedLine(original_type="user", reason="invalid compact summary")
        blocks, was_string = _parse_user_blocks(msg.get("content"))
        try:
            line = UserLine.model_validate(obj)
        except ValidationError as e:
            logger.debug("user line validation failed: %s", e)
            return SkippedLine(original_type="user", reason=f"validation: {e.errors()[0]['msg']}")
        line.content_blocks = blocks
        line.original_content_was_string = was_string
        return line

    if t == "assistant":
        msg = obj.get("message") or {}
        if not isinstance(msg, dict):
            return SkippedLine(original_type="assistant", reason="missing message")
        blocks = _parse_assistant_blocks(msg.get("content"))
        try:
            line = AssistantLine.model_validate({**obj, "model": msg.get("model")})
        except ValidationError as e:
            logger.debug("assistant line validation failed: %s", e)
            return SkippedLine(
                original_type="assistant", reason=f"validation: {e.errors()[0]['msg']}"
            )
        line.content_blocks = blocks
        return line

    return SkippedLine(original_type=str(t), reason=f"unknown top-level type {t!r}")
