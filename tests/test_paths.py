import json
from pathlib import Path

import pytest

from claude_md_transcripts.paths import (
    claude_projects_dir,
    default_output_dir_for,
    default_output_root,
    default_subdir_name,
    encode_host_path,
    encode_host_path_as_subdir,
    recover_host_path,
    resolve_session_dir,
)


def _write_session_with_cwd(path: Path, cwd: str) -> None:
    """
    Write a minimal one-line JSONL session that carries a ``cwd`` field.
    """
    path.write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n", encoding="utf-8")


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


def test_default_output_root_returns_claude_md_transcripts_dir():
    assert default_output_root() == Path.home() / ".claude" / "claude-md-transcripts"


def test_encode_host_path_as_subdir_replaces_slashes_with_underscores():
    """``/`` becomes ``_`` and the leading ``/`` is dropped."""
    assert (
        encode_host_path_as_subdir(Path("/Users/david.sanders/projects/qmd"))
        == "Users_david.sanders_projects_qmd"
    )


def test_encode_host_path_as_subdir_preserves_dots():
    """Unlike Claude Code's encoding, ``.`` survives intact."""
    out = encode_host_path_as_subdir(Path("/Users/david.sanders/projects/foo.bar"))
    assert "." in out
    assert out == "Users_david.sanders_projects_foo.bar"


def test_encode_host_path_as_subdir_distinguishes_collision_pairs():
    """The two paths that collide under Claude Code's encoding stay distinct here."""
    a = encode_host_path_as_subdir(Path("/a/b.c"))
    b = encode_host_path_as_subdir(Path("/a.b/c"))
    assert a != b


def test_recover_host_path_reads_first_cwd(tmp_path):
    sd = tmp_path / "-some-encoded-dir"
    sd.mkdir()
    _write_session_with_cwd(sd / "abc.jsonl", "/Users/fake/projects/qmd")
    assert recover_host_path(sd) == Path("/Users/fake/projects/qmd")


def test_recover_host_path_skips_malformed_lines(tmp_path):
    sd = tmp_path / "-some-encoded-dir"
    sd.mkdir()
    jsonl = sd / "abc.jsonl"
    jsonl.write_text(
        "not-json\n"
        + json.dumps({"type": "permission-mode"})  # no cwd
        + "\n"
        + json.dumps({"type": "user", "cwd": "/Users/fake/projects/foo"})
        + "\n",
        encoding="utf-8",
    )
    assert recover_host_path(sd) == Path("/Users/fake/projects/foo")


def test_recover_host_path_returns_none_when_no_cwd(tmp_path):
    sd = tmp_path / "-empty"
    sd.mkdir()
    (sd / "abc.jsonl").write_text(
        json.dumps({"type": "permission-mode"}) + "\n", encoding="utf-8"
    )
    assert recover_host_path(sd) is None


def test_recover_host_path_returns_none_when_no_jsonls(tmp_path):
    sd = tmp_path / "-empty"
    sd.mkdir()
    assert recover_host_path(sd) is None


def test_default_subdir_name_uses_recovered_host_path(tmp_path):
    sd = tmp_path / "-Users-fake-projects-my-tool"
    sd.mkdir()
    _write_session_with_cwd(sd / "abc.jsonl", "/Users/fake/projects/my-tool")
    assert default_subdir_name(sd) == "Users_fake_projects_my-tool"


def test_default_subdir_name_preserves_dots_in_recovered_path(tmp_path):
    sd = tmp_path / "-Users-david-sanders-projects-qmd"
    sd.mkdir()
    _write_session_with_cwd(sd / "abc.jsonl", "/Users/david.sanders/projects/qmd")
    assert default_subdir_name(sd) == "Users_david.sanders_projects_qmd"


def test_default_subdir_name_falls_back_to_encoded_name_when_no_cwd(tmp_path):
    sd = tmp_path / "-no-cwd"
    sd.mkdir()
    (sd / "abc.jsonl").write_text(
        json.dumps({"type": "permission-mode"}) + "\n", encoding="utf-8"
    )
    assert default_subdir_name(sd) == "no-cwd"


def test_default_subdir_name_falls_back_to_unknown_for_bare_dash(tmp_path):
    sd = tmp_path / "-"
    sd.mkdir()
    assert default_subdir_name(sd) == "unknown"


def test_default_output_dir_for_composes_root_and_subdir(tmp_path):
    sd = tmp_path / "-Users-david-sanders-projects-qmd"
    sd.mkdir()
    _write_session_with_cwd(sd / "abc.jsonl", "/Users/david.sanders/projects/qmd")
    assert (
        default_output_dir_for(sd)
        == Path.home()
        / ".claude"
        / "claude-md-transcripts"
        / "Users_david.sanders_projects_qmd"
    )
