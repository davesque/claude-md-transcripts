from pathlib import Path

import pytest

from claude_md_transcripts.paths import (
    claude_projects_dir,
    encode_host_path,
    output_dir_for_collection,
    resolve_session_dir,
)


def test_encode_basic_unix_path():
    assert (
        encode_host_path(Path("/Users/davidsanders/projects/qmd"))
        == "-Users-davidsanders-projects-qmd"
    )


def test_encode_replaces_dots_with_dashes():
    """User directories with dots in them (e.g. 'david.sanders') get dashed."""
    assert (
        encode_host_path(Path("/Users/david.sanders/projects/qmd"))
        == "-Users-david-sanders-projects-qmd"
    )


def test_encode_handles_dot_directories():
    """A '/.claude/foo' path becomes '--claude-foo' (slash + dot both dashed)."""
    encoded = encode_host_path(
        Path("/Users/david-sanders/projects/nexus/.claude/worktrees/nexus-skill-evals")
    )
    assert encoded == "-Users-david-sanders-projects-nexus--claude-worktrees-nexus-skill-evals"


def test_encode_strips_trailing_slash(tmp_path):
    assert encode_host_path(Path("/foo/bar/")) == "-foo-bar"


def test_encode_resolves_relative_to_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    encoded = encode_host_path(Path("subdir"))
    assert encoded.endswith("-subdir")


def test_claude_projects_dir_default():
    assert claude_projects_dir() == Path.home() / ".claude" / "projects"


def test_resolve_session_dir_returns_existing():
    qmd_dir = resolve_session_dir(Path("/Users/david.sanders/projects/qmd"))
    assert qmd_dir.exists()
    assert qmd_dir.name == "-Users-david-sanders-projects-qmd"


def test_resolve_session_dir_raises_when_missing(tmp_path):
    fake = tmp_path / "no-such-project"
    fake.mkdir()
    with pytest.raises(FileNotFoundError):
        resolve_session_dir(fake)


def test_output_dir_for_collection():
    out = output_dir_for_collection("my-collection")
    assert out == Path.home() / ".claude" / "qmd-transcripts" / "my-collection"
