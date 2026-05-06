"""
Map host project paths to Claude Code's encoded session directories.

Also provides defaults for the markdown output root and the per-project
subdirectory naming used by the CLI.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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


def recover_host_path(session_dir: Path) -> Path | None:
    """
    Read JSONLs in ``session_dir`` until a ``cwd`` field is found.

    Walks the session files in sorted order, falling back to subsequent
    lines (and subsequent files) if the first record is malformed or has
    no ``cwd``. Returns the recovered absolute host path as a ``Path``,
    or ``None`` if no session yields a usable ``cwd``.
    """
    for jsonl in sorted(session_dir.glob("*.jsonl")):
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


def encode_host_path_as_subdir(host_path: Path) -> str:
    """
    Encode an absolute host path as a flat, non-lossy subdirectory name.

    Drops the leading ``/`` and replaces remaining ``/`` with ``_``;
    ``.`` is preserved. Example::

        /Users/david.sanders/projects/qmd → Users_david.sanders_projects_qmd

    This avoids the basename-collision and ``/`` vs ``.`` ambiguity that
    Claude Code's own encoding (used for ``~/.claude/projects/``) suffers
    from. Two distinct host paths always produce distinct subdir names,
    modulo paths whose components contain literal ``_`` (rare in practice;
    callers can pass ``--output-dir`` to disambiguate).
    """
    return str(host_path).lstrip("/").replace("/", "_")


def default_subdir_name(session_dir: Path) -> str:
    """
    Derive an unambiguous subdirectory name for a session directory.

    Tries to recover the project's actual host path from the JSONLs'
    ``cwd`` field via :func:`recover_host_path` and encodes it via
    :func:`encode_host_path_as_subdir`. For example::

        ~/.claude/projects/-Users-david-sanders-projects-qmd
            → Users_david.sanders_projects_qmd

    Falls back to the encoded session-dir name (with the leading ``-``
    stripped) when no JSONL yields a usable ``cwd``. Falls further back
    to ``"unknown"`` if the encoded name is empty.
    """
    host_path = recover_host_path(session_dir)
    if host_path is not None:
        return encode_host_path_as_subdir(host_path)
    return session_dir.name.lstrip("-") or "unknown"


def default_output_dir_for(session_dir: Path) -> Path:
    """
    Compose the default output directory for a session directory.

    Equivalent to ``default_output_root() / default_subdir_name(session_dir)``.
    """
    return default_output_root() / default_subdir_name(session_dir)
