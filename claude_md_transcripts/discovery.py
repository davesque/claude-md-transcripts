"""
Find Claude Code projects on disk and report metadata about each.

Used by the interactive TUI in :mod:`picker` so the user can pick projects
to export without remembering encoded directory names. The original host
path is recovered, when possible, by reading the ``cwd`` field of the
first parseable JSONL line in each project directory; when no JSONL line
yields a ``cwd``, the basename falls back to the encoded directory's
trailing segment.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectInfo:
    """
    Summary of one Claude Code project directory under ``~/.claude/projects/``.

    Attributes
    ----------
    session_dir
        The encoded directory containing JSONL sessions for this project.
    host_path
        The original host path the project was opened from, recovered from
        the ``cwd`` field of the first parseable session line. ``None`` if
        no line yielded a ``cwd``.
    basename
        A human-friendly display name (the project's host-path basename
        when available, otherwise derived from the encoded directory).
    session_count
        Number of ``*.jsonl`` files in the directory.
    total_size
        Sum of those files' byte sizes.
    """

    session_dir: Path
    host_path: Path | None
    basename: str
    session_count: int
    total_size: int

    def format_size(self) -> str:
        """
        Return a short human-readable size string.
        """
        if self.total_size >= 1_000_000:
            return f"{self.total_size / 1_000_000:.1f} MB"
        if self.total_size >= 1_000:
            return f"{self.total_size / 1_000:.0f} KB"
        return f"{self.total_size} B"


def discover_projects(root: Path) -> list[ProjectInfo]:
    """
    Scan ``root`` for Claude Code project directories and summarize each.

    Parameters
    ----------
    root
        Typically ``Path.home() / ".claude" / "projects"``. Pass a temp
        directory in tests.

    Returns
    -------
    list[ProjectInfo]
        Sorted by ``basename`` (case-insensitive). Directories with no
        ``*.jsonl`` files are excluded.
    """
    if not root.exists():
        return []
    out: list[ProjectInfo] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        sessions = sorted(d.glob("*.jsonl"))
        if not sessions:
            continue
        host_path = _recover_host_path(sessions)
        basename = host_path.name if host_path else _basename_from_encoded(d.name)
        total_size = sum(p.stat().st_size for p in sessions)
        out.append(
            ProjectInfo(
                session_dir=d,
                host_path=host_path,
                basename=basename,
                session_count=len(sessions),
                total_size=total_size,
            )
        )
    out.sort(key=lambda p: p.basename.lower())
    return out


def _recover_host_path(sessions: list[Path]) -> Path | None:
    """
    Read sessions until a ``cwd`` field is found, then return it as a Path.

    Tries the first line of each session, falling back to subsequent lines
    if the first is malformed or has no ``cwd``. Returns ``None`` if no
    session yields a value.
    """
    for jsonl in sessions:
        try:
            with jsonl.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = obj.get("cwd") if isinstance(obj, dict) else None
                    if isinstance(cwd, str) and cwd:
                        return Path(cwd)
        except OSError as e:
            logger.debug("could not read %s: %s", jsonl, e)
            continue
    return None


def _basename_from_encoded(encoded: str) -> str:
    """
    Best-effort basename when no ``cwd`` is available.

    Picks the segment after the last ``-`` in the encoded directory name.
    Inevitably wrong for hyphenated host basenames, but only used as a
    last resort.
    """
    stripped = encoded.lstrip("-")
    if "-" not in stripped:
        return stripped
    return stripped.rsplit("-", 1)[-1] or stripped
