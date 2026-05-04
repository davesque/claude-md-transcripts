from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_md_transcripts.discovery import ProjectInfo, discover_projects


def _write_session(target: Path, cwd: str | None) -> None:
    """Write a tiny session JSONL with the given cwd in its first line."""
    line = {
        "type": "user",
        "uuid": "u1",
        "parentUuid": None,
        "timestamp": "2026-05-01T10:00:00.000Z",
        "sessionId": "s",
        "isSidechain": False,
        "message": {"role": "user", "content": "hi"},
    }
    if cwd is not None:
        line["cwd"] = cwd
    target.write_text(json.dumps(line) + "\n")


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def test_discover_returns_one_entry_per_dir_with_sessions(projects_root: Path):
    foo = projects_root / "-Users-foo-projects-foo"
    foo.mkdir()
    _write_session(foo / "a.jsonl", cwd="/Users/foo/projects/foo")
    _write_session(foo / "b.jsonl", cwd="/Users/foo/projects/foo")

    bar = projects_root / "-Users-foo-projects-hyphenated-name"
    bar.mkdir()
    _write_session(bar / "c.jsonl", cwd="/Users/foo/projects/hyphenated-name")

    out = discover_projects(projects_root)
    assert len(out) == 2
    by_basename = {p.basename: p for p in out}
    assert "foo" in by_basename
    assert "hyphenated-name" in by_basename
    foo_info = by_basename["foo"]
    assert foo_info.session_count == 2
    assert foo_info.host_path == Path("/Users/foo/projects/foo")
    assert foo_info.total_size > 0


def test_discover_skips_dirs_without_sessions(projects_root: Path):
    (projects_root / "-empty").mkdir()
    populated = projects_root / "-something"
    populated.mkdir()
    _write_session(populated / "a.jsonl", cwd="/Users/x/something")

    out = discover_projects(projects_root)
    assert {p.basename for p in out} == {"something"}


def test_discover_falls_back_to_encoded_name_when_cwd_missing(projects_root: Path):
    d = projects_root / "-Users-foo-projects-no-cwd"
    d.mkdir()
    _write_session(d / "a.jsonl", cwd=None)
    out = discover_projects(projects_root)
    assert len(out) == 1
    info = out[0]
    assert info.host_path is None
    # Fallback basename comes from the encoded directory's last hyphen segment.
    assert info.basename == "no-cwd" or info.basename == "cwd"


def test_discover_handles_unreadable_jsonl(projects_root: Path):
    d = projects_root / "-broken"
    d.mkdir()
    (d / "a.jsonl").write_bytes(b"not json\n")
    out = discover_projects(projects_root)
    assert len(out) == 1
    assert out[0].session_count == 1
    assert out[0].host_path is None  # cwd couldn't be read


def test_discover_results_sorted_by_basename_case_insensitive(projects_root: Path):
    for name, cwd in (("Zeta", "/p/Zeta"), ("alpha", "/p/alpha"), ("Beta", "/p/Beta")):
        d = projects_root / f"-{name}"
        d.mkdir()
        _write_session(d / "a.jsonl", cwd=cwd)
    out = discover_projects(projects_root)
    assert [p.basename for p in out] == ["alpha", "Beta", "Zeta"]


def test_discover_total_size_is_sum_of_session_files(projects_root: Path):
    d = projects_root / "-bigproj"
    d.mkdir()
    _write_session(d / "a.jsonl", cwd="/p/bigproj")
    _write_session(d / "b.jsonl", cwd="/p/bigproj")
    out = discover_projects(projects_root)
    assert len(out) == 1
    info = out[0]
    expected = sum(p.stat().st_size for p in d.glob("*.jsonl"))
    assert info.total_size == expected


def test_project_info_format_size():
    info = ProjectInfo(
        session_dir=Path("/x"),
        host_path=Path("/p"),
        basename="x",
        session_count=1,
        total_size=1500,
    )
    assert info.format_size().endswith("KB")
    big = ProjectInfo(
        session_dir=Path("/x"),
        host_path=Path("/p"),
        basename="x",
        session_count=1,
        total_size=5_000_000,
    )
    assert big.format_size().endswith("MB")
