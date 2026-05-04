"""
Stream a Claude Code session JSONL file into typed records.

The reader is intentionally tolerant: malformed JSON lines are counted but
do not abort the run, oversized files are skipped with a flag, and unknown
line types fall through to :class:`SkippedLine`. Diagnostics live on
:class:`ReaderResult` so callers can surface them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .schema import (
    CustomTitleLine,
    ParsedLine,
    SkippedLine,
    parse_line,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES: int = 50 * 1024 * 1024


@dataclass(frozen=True)
class SessionRecord:
    """
    One JSONL line, parsed into a typed model with positional metadata.

    Attributes
    ----------
    line_number
        1-indexed line number in the source file.
    parsed
        The typed line model.
    """

    line_number: int
    parsed: ParsedLine


@dataclass
class ReaderResult:
    """
    Aggregate output of :func:`read_session`.

    Attributes
    ----------
    path
        The source JSONL path.
    size_bytes
        File size on disk.
    records
        Parsed records in source order. Empty if the file was skipped for size.
    skipped_for_size
        True if the file exceeded ``max_bytes`` and was not read.
    parse_errors
        Count of lines that failed JSON decoding.
    """

    path: Path
    size_bytes: int
    records: list[SessionRecord] = field(default_factory=list)
    skipped_for_size: bool = False
    parse_errors: int = 0

    @property
    def skipped_count(self) -> int:
        """
        Number of records classified as :class:`SkippedLine`.
        """
        return sum(1 for r in self.records if isinstance(r.parsed, SkippedLine))

    @property
    def session_id(self) -> str | None:
        """
        Session identifier inferred from the first record that carries one.
        """
        for r in self.records:
            sid = getattr(r.parsed, "session_id", None)
            if sid:
                return sid
        return None

    @property
    def custom_title(self) -> str | None:
        """
        First ``custom-title`` value seen in the session, if any.
        """
        for r in self.records:
            if isinstance(r.parsed, CustomTitleLine):
                return r.parsed.title
        return None

    def iter_kept(self) -> Iterator[SessionRecord]:
        """
        Iterate over records that aren't auxiliary skips.
        """
        for r in self.records:
            if not isinstance(r.parsed, SkippedLine):
                yield r


def read_session(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> ReaderResult:
    """
    Parse one Claude Code session JSONL file into typed records.

    Parameters
    ----------
    path
        Path to the session JSONL file.
    max_bytes
        Skip the file with a warning if its raw size exceeds this. Defaults
        to 50 MB, which has historically meant the file is dominated by
        Playwright screenshots or replayed compaction context.

    Returns
    -------
    ReaderResult
    """
    size = path.stat().st_size
    result = ReaderResult(path=path, size_bytes=size)
    if size > max_bytes:
        logger.warning(
            "skipping %s: %.1f MB exceeds max_bytes %.1f MB",
            path,
            size / 1e6,
            max_bytes / 1e6,
        )
        result.skipped_for_size = True
        return result

    with path.open("r", encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning("%s:%d: invalid JSON (%s); skipping line", path, ln, e)
                result.parse_errors += 1
                continue
            if not isinstance(obj, dict):
                result.parse_errors += 1
                continue
            try:
                parsed = parse_line(obj)
            except Exception as e:
                logger.exception("%s:%d: unexpected error parsing line: %s", path, ln, e)
                parsed = SkippedLine(original_type=str(obj.get("type")), reason=f"exception: {e}")
            result.records.append(SessionRecord(line_number=ln, parsed=parsed))
    return result
