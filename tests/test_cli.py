from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from claude_md_transcripts.cli import cli


def _write_minimal_session(
    target: Path, session_id: str = "11111111-1111-1111-1111-111111111111"
) -> None:
    payloads = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "timestamp": "2026-05-01T10:00:00.000Z",
            "sessionId": session_id,
            "isSidechain": False,
            "message": {"role": "user", "content": "Investigate slow query"},
        },
    ]
    target.write_text("\n".join(json.dumps(p) for p in payloads) + "\n")


def test_inspect_reports_summary(tmp_path: Path):
    p = tmp_path / "x.jsonl"
    _write_minimal_session(p)
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect", str(p)])
    assert result.exit_code == 0
    assert "session_id" in result.output
    assert "kept" in result.output
    assert "skipped" in result.output


def test_sync_reports_summary(tmp_path: Path, monkeypatch):
    """End-to-end sync, with qmd subprocess stubbed via env."""
    # Build a fake claude-projects dir and point the orchestrator at it.
    claude_root = tmp_path / "claude-projects"
    encoded = claude_root / "-fake-host-project"
    encoded.mkdir(parents=True)
    _write_minimal_session(encoded / "11111111-1111-1111-1111-111111111111.jsonl")

    # Redirect Path.home so paths.resolve_session_dir / output_dir_for_collection
    # both land under tmp_path.
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "projects").mkdir(parents=True)
    # Symlink the fake encoded dir into the fake home so resolve_session_dir finds it.
    target = fake_home / ".claude" / "projects" / "-fake-host-project"
    target.symlink_to(encoded)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    # Stub QmdClient via env-controlled fake binary
    fake_qmd = tmp_path / "fake-qmd.sh"
    fake_qmd.write_text("#!/bin/sh\nexit 0\n")
    fake_qmd.chmod(0o755)

    monkeypatch.setenv("CLAUDE_MD_TRANSCRIPTS_QMD_BIN", str(fake_qmd))

    # session_dir = encoded; pass it directly via --session-dir to avoid host_path mapping
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "sync",
            "--session-dir",
            str(encoded),
            "--collection",
            "test-coll",
            "--description",
            "Test",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "test-coll" in result.output
    assert "converted" in result.output.lower()
    out_dir = fake_home / ".claude" / "qmd-transcripts" / "test-coll"
    assert out_dir.exists()
    assert any(out_dir.glob("*.md"))


def test_sync_all_iterates_session_dirs(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)

    for name, sid in (
        ("-Users-fake-projects-foo", "aaaaaaaa-1111-1111-1111-111111111111"),
        ("-Users-fake-projects-bar", "bbbbbbbb-2222-2222-2222-222222222222"),
    ):
        d = proj_dir / name
        d.mkdir()
        _write_minimal_session(d / f"{sid}.jsonl", session_id=sid)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    fake_qmd = tmp_path / "fake-qmd.sh"
    fake_qmd.write_text("#!/bin/sh\nexit 0\n")
    fake_qmd.chmod(0o755)
    monkeypatch.setenv("CLAUDE_MD_TRANSCRIPTS_QMD_BIN", str(fake_qmd))

    runner = CliRunner()
    result = runner.invoke(cli, ["sync-all"])
    assert result.exit_code == 0, result.output
    assert "foo-claude-sessions" in result.output
    assert "bar-claude-sessions" in result.output

    out_root = fake_home / ".claude" / "qmd-transcripts"
    assert (out_root / "foo-claude-sessions").exists()
    assert (out_root / "bar-claude-sessions").exists()


def test_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("sync", "sync-all", "inspect"):
        assert cmd in result.output
