from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from claude_md_transcripts.cli import cli
from claude_md_transcripts.paths import encode_host_path


def _write_minimal_session(
    target: Path,
    session_id: str = "11111111-1111-1111-1111-111111111111",
    cwd: str | None = None,
) -> None:
    payloads = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "timestamp": "2026-05-01T10:00:00.000Z",
            "sessionId": session_id,
            "isSidechain": False,
            "cwd": cwd or "/Users/fake/projects/foo",
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


def test_export_with_session_dir_and_explicit_output_dir(tmp_path: Path):
    encoded = tmp_path / "claude-projects" / "-fake-host-project"
    encoded.mkdir(parents=True)
    _write_minimal_session(encoded / "11111111-1111-1111-1111-111111111111.jsonl")

    out_dir = tmp_path / "exports" / "test-out"

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["export", "--session-dir", str(encoded), "--output-dir", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    assert "converted" in result.output.lower()
    assert out_dir.exists()
    assert any(out_dir.glob("*.md"))


def test_export_with_host_path_uses_default_output_dir(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)
    encoded_name = "-Users-fake-projects-foo"
    d = proj_dir / encoded_name
    d.mkdir()
    _write_minimal_session(d / "aaaaaaaa-1111-1111-1111-111111111111.jsonl")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    # Symlink so resolve_session_dir matches encode_host_path of HOST_PATH.
    host_path = tmp_path / "Users" / "fake" / "projects" / "foo"
    host_path.mkdir(parents=True)
    encoded_for_host = encode_host_path(host_path)
    (proj_dir / encoded_for_host).symlink_to(d)

    runner = CliRunner()
    result = runner.invoke(cli, ["export", str(host_path)])
    assert result.exit_code == 0, result.output

    expected_out = (
        fake_home / ".claude" / "claude-md-transcripts" / "Users_fake_projects_foo"
    )
    assert expected_out.exists()
    assert any(expected_out.glob("*.md"))


def test_export_all_iterates_session_dirs(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)

    for name, sid, cwd in (
        (
            "-Users-fake-projects-foo",
            "aaaaaaaa-1111-1111-1111-111111111111",
            "/Users/fake/projects/foo",
        ),
        (
            "-Users-fake-projects-bar",
            "bbbbbbbb-2222-2222-2222-222222222222",
            "/Users/fake/projects/bar",
        ),
    ):
        d = proj_dir / name
        d.mkdir()
        _write_minimal_session(d / f"{sid}.jsonl", session_id=sid, cwd=cwd)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    runner = CliRunner()
    result = runner.invoke(cli, ["export-all"])
    assert result.exit_code == 0, result.output
    assert "foo" in result.output
    assert "bar" in result.output

    out_root = fake_home / ".claude" / "claude-md-transcripts"
    assert (out_root / "Users_fake_projects_foo").exists()
    assert (out_root / "Users_fake_projects_bar").exists()


def test_export_all_with_explicit_output_root(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)
    d = proj_dir / "-Users-fake-projects-foo"
    d.mkdir()
    _write_minimal_session(
        d / "aaaaaaaa-1111-1111-1111-111111111111.jsonl",
        cwd="/Users/fake/projects/foo",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    custom_root = tmp_path / "custom"

    runner = CliRunner()
    result = runner.invoke(cli, ["export-all", "--output-dir", str(custom_root)])
    assert result.exit_code == 0, result.output

    out = custom_root / "Users_fake_projects_foo"
    assert out.exists()
    assert any(out.glob("*.md"))


def test_help_lists_renamed_subcommands():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("export", "export-all", "retitle", "retitle-all", "inspect"):
        assert cmd in result.output
    assert "sync" not in result.output


def test_export_without_args_in_non_tty_raises_usage_error():
    """When stdin/stdout aren't TTYs, export without HOST_PATH refuses to run."""
    runner = CliRunner()
    result = runner.invoke(cli, ["export"])
    assert result.exit_code != 0
    assert "HOST_PATH" in result.output or "session-dir" in result.output


def test_export_rejects_output_dir_in_interactive_mode(tmp_path: Path, monkeypatch):
    """Passing --output-dir with no HOST_PATH/--session-dir is a usage error."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    import claude_md_transcripts.cli as cli_module

    monkeypatch.setattr(cli_module, "is_tty", lambda: True)

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "--output-dir", str(tmp_path / "out")])
    assert result.exit_code != 0
    assert "output-dir" in result.output.lower()


def test_export_interactive_mode_invokes_picker(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)
    for name, sid, cwd in (
        (
            "-Users-fake-projects-foo",
            "aaaaaaaa-1111-1111-1111-111111111111",
            "/Users/fake/projects/foo",
        ),
        (
            "-Users-fake-projects-bar",
            "bbbbbbbb-2222-2222-2222-222222222222",
            "/Users/fake/projects/bar",
        ),
    ):
        d = proj_dir / name
        d.mkdir()
        _write_minimal_session(d / f"{sid}.jsonl", session_id=sid, cwd=cwd)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    import claude_md_transcripts.cli as cli_module

    monkeypatch.setattr(cli_module, "is_tty", lambda: True)

    captured: dict = {}

    def fake_pick_projects(projects, *, prompter=None):
        captured["projects"] = projects
        return list(projects)

    monkeypatch.setattr(cli_module, "pick_projects", fake_pick_projects)

    runner = CliRunner()
    result = runner.invoke(cli, ["export"])
    assert result.exit_code == 0, result.output

    basenames = {p.basename for p in captured["projects"]}
    assert {"foo", "bar"} <= basenames

    out_root = fake_home / ".claude" / "claude-md-transcripts"
    assert (out_root / "Users_fake_projects_foo").exists()
    assert (out_root / "Users_fake_projects_bar").exists()
    assert "Totals across 2 projects" in result.output


def test_export_interactive_cancel_prints_cancelled(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)
    d = proj_dir / "-Users-fake-projects-foo"
    d.mkdir()
    _write_minimal_session(d / "aaaaaaaa-1111-1111-1111-111111111111.jsonl")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    import claude_md_transcripts.cli as cli_module

    monkeypatch.setattr(cli_module, "is_tty", lambda: True)
    monkeypatch.setattr(cli_module, "pick_projects", lambda p, prompter=None: None)

    runner = CliRunner()
    result = runner.invoke(cli, ["export"])
    assert result.exit_code == 0
    assert "Cancelled" in result.output


def test_export_interactive_empty_selection_prints_nothing_selected(
    tmp_path: Path, monkeypatch
):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)
    d = proj_dir / "-Users-fake-projects-foo"
    d.mkdir()
    _write_minimal_session(d / "aaaaaaaa-1111-1111-1111-111111111111.jsonl")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    import claude_md_transcripts.cli as cli_module

    monkeypatch.setattr(cli_module, "is_tty", lambda: True)
    monkeypatch.setattr(cli_module, "pick_projects", lambda p, prompter=None: [])

    runner = CliRunner()
    result = runner.invoke(cli, ["export"])
    assert result.exit_code == 0
    assert "Nothing selected" in result.output


def test_retitle_with_explicit_output_dir(tmp_path: Path, monkeypatch):
    """retitle on an output dir with no markdown files exits cleanly."""
    out_dir = tmp_path / "exports" / "test-out"
    out_dir.mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(cli, ["retitle", "--output-dir", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert "total=0" in result.output
    assert "retitled=0" in result.output


def test_retitle_without_args_raises_usage_error():
    """retitle with neither HOST_PATH nor --output-dir is a usage error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["retitle"])
    assert result.exit_code != 0
    assert "HOST_PATH" in result.output or "output-dir" in result.output


def test_retitle_all_walks_default_root_and_reports_no_subdirs(tmp_path: Path, monkeypatch):
    """retitle-all on a missing default root prints a no-root message."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    runner = CliRunner()
    result = runner.invoke(cli, ["retitle-all"])
    assert result.exit_code == 0
    assert "No transcripts root" in result.output


def test_retitle_all_with_explicit_empty_root(tmp_path: Path):
    """retitle-all on an explicitly empty root prints 'No subdirectories found.'"""
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli, ["retitle-all", "--output-dir", str(empty_root)])
    assert result.exit_code == 0
    assert "No subdirectories found" in result.output


def test_retitle_all_with_populated_root(tmp_path, monkeypatch):
    """retitle-all over a root with subdirs runs Exporter.retitle on each
    and prints a totals line."""
    from claude_md_transcripts.exporter import RetitleResult

    root = tmp_path / "transcripts-root"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir(parents=True)
    (root / "alpha" / "doc.md").write_text("---\nsession_id: a\n---\nbody\n")
    (root / "beta" / "doc.md").write_text("---\nsession_id: b\n---\nbody\n")

    calls: list = []

    def fake_retitle(self, output_dir, *, force=False):
        calls.append((Path(output_dir), force))
        return RetitleResult(
            output_dir=output_dir,
            files_total=1,
            files_retitled=1,
        )

    monkeypatch.setattr(
        "claude_md_transcripts.exporter.Exporter.retitle",
        fake_retitle,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["retitle-all", "--output-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert "Totals" in result.output
    assert "retitled=2" in result.output
    assert {c[0].name for c in calls} == {"alpha", "beta"}
