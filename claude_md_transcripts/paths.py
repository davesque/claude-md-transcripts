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


def default_output_root() -> Path:
    """
    Return the default root directory for exported markdown.

    All exports land under this directory, with one subdirectory per
    host project (named by basename) unless the caller overrides with
    an explicit ``--output-dir``.
    """
    return Path.home() / ".claude" / "claude-md-transcripts"


def default_subdir_name(session_dir: Path) -> str:
    """
    Derive a sensible subdirectory name from a Claude Code session directory.

    The encoded directory ``-Users-foo-projects-qmd`` becomes ``qmd``.
    Falls back to ``"unknown"`` if no usable basename can be extracted.
    """
    name = session_dir.name.lstrip("-")
    basename = name.rsplit("-", 1)[-1] if "-" in name else name
    return basename or "unknown"


def default_output_dir_for(session_dir: Path) -> Path:
    """
    Compose the default output directory for a session directory.

    Equivalent to ``default_output_root() / default_subdir_name(session_dir)``.
    """
    return default_output_root() / default_subdir_name(session_dir)
