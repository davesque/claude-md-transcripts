from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_md_transcripts.qmd import QmdClient
from claude_md_transcripts.render import RenderConfig
from claude_md_transcripts.sync import (
    SyncOrchestrator,
    default_collection_name,
)
from tests.test_qmd import FakeResult, FakeRunner


def write_minimal_session(
    target: Path, session_id: str = "11111111-1111-1111-1111-111111111111"
) -> None:
    """Write a tiny but parseable session file."""
    payloads = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "timestamp": "2026-05-01T10:00:00.000Z",
            "sessionId": session_id,
            "isSidechain": False,
            "message": {"role": "user", "content": "How do I add a column?"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "parentUuid": "u1",
            "timestamp": "2026-05-01T10:00:01.000Z",
            "sessionId": session_id,
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "model": "claude-x",
                "content": [{"type": "text", "text": "Use ALTER TABLE."}],
            },
        },
    ]
    with target.open("w") as f:
        for p in payloads:
            f.write(json.dumps(p) + "\n")


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "claude-projects" / "-fake-host-project"
    d.mkdir(parents=True)
    write_minimal_session(d / "11111111-1111-1111-1111-111111111111.jsonl")
    write_minimal_session(
        d / "22222222-2222-2222-2222-222222222222.jsonl",
        session_id="22222222-2222-2222-2222-222222222222",
    )
    return d


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "qmd-transcripts" / "test-collection"


@pytest.fixture
def orchestrator(output_dir: Path) -> SyncOrchestrator:
    runner = FakeRunner(
        [
            FakeResult(returncode=1),  # collection_exists -> False
            FakeResult(returncode=0),  # collection_add
            FakeResult(returncode=0),  # context_add
            FakeResult(returncode=0),  # update
        ]
    )
    return SyncOrchestrator(
        qmd=QmdClient(runner=runner),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
    )


def test_default_collection_name_derived_from_session_dir():
    sd = Path("/some/where/-Users-david-sanders-projects-qmd")
    assert default_collection_name(sd) == "qmd-claude-sessions"


def test_default_collection_name_handles_short_paths(tmp_path):
    sd = tmp_path / "-foo"
    sd.mkdir()
    assert default_collection_name(sd) == "foo-claude-sessions"


def test_sync_writes_one_markdown_per_jsonl(
    orchestrator: SyncOrchestrator, session_dir: Path, output_dir: Path
):
    result = orchestrator.sync_session_dir(
        session_dir,
        collection="test-collection",
        description="Test sessions",
    )
    assert result.files_total == 2
    assert result.files_converted == 2
    md_files = sorted(output_dir.glob("*.md"))
    assert len(md_files) == 2


def test_sync_creates_output_dir(
    orchestrator: SyncOrchestrator, session_dir: Path, output_dir: Path
):
    assert not output_dir.exists()
    orchestrator.sync_session_dir(session_dir, collection="test-collection", description="x")
    assert output_dir.exists()


def _starts_with(cmd: list[str], prefix: list[str]) -> bool:
    return cmd[: len(prefix)] == prefix


def test_sync_calls_qmd_lifecycle(session_dir: Path, output_dir: Path):
    runner = FakeRunner(
        [
            FakeResult(returncode=1),  # collection_exists -> False (missing)
            FakeResult(returncode=0),  # collection_add
            FakeResult(returncode=0),  # context_add
            FakeResult(returncode=0),  # update
        ]
    )
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=runner),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
    )
    orch.sync_session_dir(session_dir, collection="test-collection", description="Test sessions")
    cmds = [c[1:] for c in runner.calls]  # drop binary name
    assert ["collection", "show", "test-collection"] in cmds
    assert any(_starts_with(c, ["collection", "add"]) for c in cmds)
    assert any(_starts_with(c, ["context", "add"]) for c in cmds)
    assert ["update"] in cmds


def test_sync_does_not_re_add_existing_collection(session_dir: Path, output_dir: Path):
    runner = FakeRunner(
        [
            FakeResult(returncode=0),  # collection_exists -> True
            FakeResult(returncode=0),  # context_add
            FakeResult(returncode=0),  # update
        ]
    )
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=runner),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
    )
    orch.sync_session_dir(session_dir, collection="test-collection", description="x")
    cmds = [c[1:] for c in runner.calls]
    assert not any(_starts_with(c, ["collection", "add"]) for c in cmds)


def test_sync_skips_context_when_description_omitted(session_dir: Path, output_dir: Path):
    runner = FakeRunner(
        [
            FakeResult(returncode=0),  # collection_exists
            FakeResult(returncode=0),  # update
        ]
    )
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=runner),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
    )
    orch.sync_session_dir(session_dir, collection="test-collection", description=None)
    cmds = [c[1:] for c in runner.calls]
    assert not any(_starts_with(c, ["context", "add"]) for c in cmds)


def test_sync_idempotent_when_input_unchanged(session_dir: Path, output_dir: Path):
    """Re-running sync without input changes does not re-render."""

    def make_runner():
        return FakeRunner([FakeResult(returncode=0)] * 10)

    orch1 = SyncOrchestrator(
        qmd=QmdClient(runner=make_runner()),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
    )
    r1 = orch1.sync_session_dir(session_dir, collection="test-collection", description="x")

    # Capture output mtimes after first run
    md_files = list(output_dir.glob("*.md"))
    initial_mtimes = {p: p.stat().st_mtime_ns for p in md_files}

    orch2 = SyncOrchestrator(
        qmd=QmdClient(runner=make_runner()),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
    )
    r2 = orch2.sync_session_dir(session_dir, collection="test-collection", description="x")

    final_mtimes = {p: p.stat().st_mtime_ns for p in output_dir.glob("*.md")}
    assert initial_mtimes == final_mtimes
    assert r1.files_converted == 2
    assert r2.files_converted == 0
    assert r2.files_unchanged == 2


def test_sync_re_renders_when_input_newer(session_dir: Path, output_dir: Path):
    runner = FakeRunner([FakeResult(returncode=0)] * 20)
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=runner),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
    )
    orch.sync_session_dir(session_dir, collection="test-collection", description="x")
    md_files = sorted(output_dir.glob("*.md"))
    assert md_files

    # Touch one input to simulate appended content
    target = next(session_dir.glob("11111111*.jsonl"))
    new_mtime = target.stat().st_mtime + 100
    import os

    os.utime(target, (new_mtime, new_mtime))

    r = orch.sync_session_dir(session_dir, collection="test-collection", description="x")
    assert r.files_converted == 1  # only the touched one
    assert r.files_unchanged == 1


def test_sync_skips_oversized_file(session_dir: Path, output_dir: Path, tmp_path: Path):
    big = session_dir / "33333333-3333-3333-3333-333333333333.jsonl"
    big.write_bytes(b"x" * 4096)  # not valid json, but the size guard fires first
    runner = FakeRunner([FakeResult(returncode=0)] * 10)
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=runner),
        render_config=RenderConfig(),
        output_root=output_dir.parent,
        max_bytes=2048,
    )
    r = orch.sync_session_dir(session_dir, collection="test-collection", description="x")
    assert r.files_skipped_for_size == 1
    assert r.files_converted == 2
