"""
Map host project paths to Claude Code's encoded session directories.

Also locates the output directory under ``~/.claude/qmd-transcripts/`` for a
given collection name.
"""

from __future__ import annotations

from pathlib import Path


def claude_projects_dir() -> Path:
    """
    Return the root directory Claude Code uses for per-project session logs.
    """
    return Path.home() / ".claude" / "projects"


def encode_host_path(host_path: Path) -> str:
    """
    Encode an absolute host path the way Claude Code does for its project dirs.

    Both ``/`` and ``.`` are replaced with ``-``. The result is what appears
    as the directory name under ``~/.claude/projects/``.

    Parameters
    ----------
    host_path
        An absolute or relative filesystem path. Relative paths are resolved
        against the current working directory before encoding.

    Returns
    -------
    str
        The encoded directory name, with no trailing dash even if the input
        had a trailing slash.
    """
    abs_path = host_path.resolve()
    s = str(abs_path)
    s = s.rstrip("/")
    return s.replace("/", "-").replace(".", "-")


def resolve_session_dir(host_path: Path) -> Path:
    """
    Return the directory under ``~/.claude/projects/`` for a host project.

    Parameters
    ----------
    host_path
        An absolute path to the host project, e.g. ``/Users/me/projects/foo``.

    Returns
    -------
    Path
        The matching session directory.

    Raises
    ------
    FileNotFoundError
        If no encoded directory exists for the given host path.
    """
    encoded = encode_host_path(host_path)
    candidate = claude_projects_dir() / encoded
    if not candidate.exists():
        raise FileNotFoundError(
            f"No Claude Code session directory for {host_path} (looked for {candidate})"
        )
    return candidate


def output_dir_for_collection(collection: str) -> Path:
    """
    Return the markdown output directory for a given collection name.
    """
    return Path.home() / ".claude" / "qmd-transcripts" / collection
