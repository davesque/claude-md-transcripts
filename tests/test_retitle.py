from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_md_transcripts.frontmatter import has_field, parse
from claude_md_transcripts.qmd import QmdClient
from claude_md_transcripts.render import RenderConfig
from claude_md_transcripts.smart_slug import SmartSlugGenerator
from claude_md_transcripts.sync import SyncOrchestrator
from tests.test_qmd import FakeResult, FakeRunner
from tests.test_smart_slug import FakeRun
from tests.test_smart_slug import FakeRunner as ClaudeFakeRunner


def write_minimal_session(
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
            "message": {"role": "user", "content": "Investigate slow query in users table"},
        },
    ]
    target.write_text("\n".join(json.dumps(p) for p in payloads) + "\n")


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions" / "-fake-project"
    d.mkdir(parents=True)
    write_minimal_session(d / "11111111-1111-1111-1111-111111111111.jsonl")
    write_minimal_session(
        d / "22222222-2222-2222-2222-222222222222.jsonl",
        session_id="22222222-2222-2222-2222-222222222222",
    )
    return d


@pytest.fixture
def output_root(tmp_path: Path) -> Path:
    return tmp_path / "qmd-transcripts"


def make_orch(
    output_root: Path,
    qmd_responses: list[FakeResult] | None,
    smart_response: FakeRun | Exception | None = None,
) -> SyncOrchestrator:
    qmd_runner = FakeRunner(qmd_responses or [FakeResult(returncode=0)] * 30)
    smart_runner = ClaudeFakeRunner(
        smart_response or FakeRun(stdout="Investigate slow user query\n")
    )
    smart_gen = SmartSlugGenerator(runner=smart_runner)
    return SyncOrchestrator(
        qmd=QmdClient(runner=qmd_runner),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=smart_gen,
    )


def test_sync_with_smart_generator_marks_frontmatter(session_dir: Path, output_root: Path):
    orch = make_orch(
        output_root, qmd_responses=None, smart_response=FakeRun(stdout="Inline smart title\n")
    )
    orch.sync_session_dir(session_dir, collection="coll1", description="x")
    md_files = sorted((output_root / "coll1").glob("*.md"))
    assert md_files
    for f in md_files:
        text = f.read_text()
        assert has_field(text, "smart_title", "true"), (
            f"frontmatter missing smart_title: {text[:200]}"
        )
    # Filename slug should reflect the smart title
    assert any("inline-smart-title" in p.name for p in md_files)


def test_sync_without_smart_generator_does_not_mark_frontmatter(
    session_dir: Path, output_root: Path
):
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
    )
    orch.sync_session_dir(session_dir, collection="coll1", description="x")
    for f in (output_root / "coll1").glob("*.md"):
        assert not has_field(f.read_text(), "smart_title", "true")


def test_retitle_collection_marks_files_smart(session_dir: Path, output_root: Path):
    # First, sync without smart generator so files are heuristic-named.
    plain = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
    )
    plain.sync_session_dir(session_dir, collection="coll1", description="x")
    md_before = sorted((output_root / "coll1").glob("*.md"))
    assert md_before
    for f in md_before:
        assert not has_field(f.read_text(), "smart_title", "true")

    # Now retitle.
    smart_orch = make_orch(
        output_root,
        qmd_responses=None,
        smart_response=FakeRun(stdout="Investigate slow user query\n"),
    )
    result = smart_orch.retitle_collection("coll1")
    assert result.files_total == len(md_before)
    assert result.files_retitled == len(md_before)

    md_after = sorted((output_root / "coll1").glob("*.md"))
    assert len(md_after) == len(md_before)
    for f in md_after:
        text = f.read_text()
        assert has_field(text, "smart_title", "true")
    # Names should now contain a slug derived from the smart title
    assert all("investigate-slow-user-query" in p.name for p in md_after)


def test_retitle_skips_already_smart_titled(session_dir: Path, output_root: Path):
    orch = make_orch(
        output_root, qmd_responses=None, smart_response=FakeRun(stdout="First pass title\n")
    )
    orch.sync_session_dir(session_dir, collection="coll1", description="x")

    smart_runner = ClaudeFakeRunner(FakeRun(stdout="Different second-pass title\n"))
    smart_gen = SmartSlugGenerator(runner=smart_runner)
    orch2 = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=smart_gen,
    )
    result = orch2.retitle_collection("coll1")
    assert result.files_skipped_already_smart == 2
    assert result.files_retitled == 0
    # Smart runner should not have been called
    assert not smart_runner.calls


def test_retitle_force_overrides_existing_smart_flag(session_dir: Path, output_root: Path):
    orch = make_orch(
        output_root, qmd_responses=None, smart_response=FakeRun(stdout="Initial title\n")
    )
    orch.sync_session_dir(session_dir, collection="coll1", description="x")

    smart_runner = ClaudeFakeRunner(FakeRun(stdout="Refreshed title\n"))
    smart_gen = SmartSlugGenerator(runner=smart_runner)
    orch2 = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=smart_gen,
    )
    result = orch2.retitle_collection("coll1", force=True)
    assert result.files_retitled == 2
    assert all("refreshed-title" in p.name for p in (output_root / "coll1").glob("*.md"))


def test_retitle_handles_smart_generator_failure(session_dir: Path, output_root: Path):
    plain = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
    )
    plain.sync_session_dir(session_dir, collection="coll1", description="x")

    # smart returns nothing
    smart_runner = ClaudeFakeRunner(FakeRun(stdout="\n"))
    smart_gen = SmartSlugGenerator(runner=smart_runner)
    orch2 = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=smart_gen,
    )
    result = orch2.retitle_collection("coll1")
    assert result.files_skipped_failed == 2
    assert result.files_retitled == 0


def test_retitle_runs_qmd_update_only_on_changes(session_dir: Path, output_root: Path):
    # Sync first with a plain orchestrator
    plain = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
    )
    plain.sync_session_dir(session_dir, collection="coll1", description="x")

    # Now retitle. We expect a `qmd update` call after retitle.
    qmd_runner = FakeRunner([FakeResult(returncode=0)] * 30)
    smart_runner = ClaudeFakeRunner(FakeRun(stdout="Title here\n"))
    smart_gen = SmartSlugGenerator(runner=smart_runner)
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=qmd_runner),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=smart_gen,
    )
    orch.retitle_collection("coll1")
    update_calls = [c for c in qmd_runner.calls if c == ["qmd", "update"]]
    assert len(update_calls) == 1

    # Second run skips all (already smart). No update call expected.
    qmd_runner2 = FakeRunner([FakeResult(returncode=0)] * 30)
    orch2 = SyncOrchestrator(
        qmd=QmdClient(runner=qmd_runner2),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=SmartSlugGenerator(runner=ClaudeFakeRunner(FakeRun(stdout="x\n"))),
    )
    orch2.retitle_collection("coll1")
    assert not any(c == ["qmd", "update"] for c in qmd_runner2.calls)


def test_retitle_requires_smart_generator(output_root: Path):
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)])),
        render_config=RenderConfig(),
        output_root=output_root,
    )
    with pytest.raises(ValueError):
        orch.retitle_collection("coll1")


def test_retitle_returns_empty_for_missing_collection(output_root: Path):
    smart_runner = ClaudeFakeRunner(FakeRun(stdout="t\n"))
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)])),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=SmartSlugGenerator(runner=smart_runner),
    )
    result = orch.retitle_collection("does-not-exist")
    assert result.files_total == 0
    assert result.files_retitled == 0


def test_retitle_preserves_other_frontmatter(session_dir: Path, output_root: Path):
    plain = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
    )
    plain.sync_session_dir(session_dir, collection="coll1", description="x")

    # Track per-session frontmatter so we can compare deterministically
    # rather than relying on glob order.
    def collect(coll_dir: Path) -> dict[str, dict[str, str]]:
        return {
            parse(p.read_text()).fields["session_id"]: parse(p.read_text()).fields
            for p in coll_dir.glob("*.md")
        }

    before_map = collect(output_root / "coll1")
    expected_keys_kept = {"session_id", "source_path", "message_count", "start_time", "end_time"}

    smart_runner = ClaudeFakeRunner(FakeRun(stdout="A new title\n"))
    smart_gen = SmartSlugGenerator(runner=smart_runner)
    orch = SyncOrchestrator(
        qmd=QmdClient(runner=FakeRunner([FakeResult(returncode=0)] * 10)),
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=smart_gen,
    )
    orch.retitle_collection("coll1")
    after_map = collect(output_root / "coll1")
    assert before_map.keys() == after_map.keys()
    for sid, before in before_map.items():
        after = after_map[sid]
        for key in expected_keys_kept & set(before):
            assert before[key] == after[key], f"field {key} for session {sid} changed"
        assert after.get("smart_title") == "true"
