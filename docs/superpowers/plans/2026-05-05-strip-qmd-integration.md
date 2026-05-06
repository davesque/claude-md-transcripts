# Strip qmd integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove qmd from `claude-md-transcripts` so the tool's only job is converting Claude Code session JSONL into clean markdown directories on disk.

**Architecture:** Keep the existing pipeline (reader → render → orchestrator) but trim the orchestrator's tail — no more qmd subprocess calls. Replace the qmd-flavored `--collection` / `--description` flags with a single `--output-dir`. Rename `sync` / `sync-all` to `export` / `export-all`. Delete `qmd.py` and `test_qmd.py` outright. Bump version to 0.2.0.

**Tech Stack:** Python 3.12+, `uv`, `click`, `pydantic`, `pytest`, `ruff`, `ty`. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-05-05-strip-qmd-design.md`.

**Working directory:** `/Users/david.sanders/projects/claude-md-transcripts`

---

## Task 1: Add new path helpers in `paths.py`

This task is purely additive. The legacy `output_dir_for_collection` stays in place (still consumed by the orchestrator) and is removed in Task 4 once nothing references it. After this task the test suite should still pass against the current code.

**Files:**
- Modify: `claude_md_transcripts/paths.py`
- Modify: `tests/test_paths.py`

- [ ] **Step 1: Write failing tests for the three new helpers**

Append to `tests/test_paths.py`:

```python
from pathlib import Path

from claude_md_transcripts.paths import (
    default_output_dir_for,
    default_output_root,
    default_subdir_name,
)


def test_default_output_root_returns_claude_md_transcripts_dir():
    assert default_output_root() == Path.home() / ".claude" / "claude-md-transcripts"


def test_default_subdir_name_extracts_basename_from_encoded_session_dir():
    sd = Path("/anywhere/-Users-david-sanders-projects-qmd")
    assert default_subdir_name(sd) == "qmd"


def test_default_subdir_name_handles_short_encoded_dir(tmp_path):
    sd = tmp_path / "-foo"
    sd.mkdir()
    assert default_subdir_name(sd) == "foo"


def test_default_subdir_name_falls_back_to_unknown_for_empty_name(tmp_path):
    sd = tmp_path / "-"
    sd.mkdir()
    assert default_subdir_name(sd) == "unknown"


def test_default_output_dir_for_composes_root_and_subdir():
    sd = Path("/anywhere/-Users-david-sanders-projects-qmd")
    assert (
        default_output_dir_for(sd)
        == Path.home() / ".claude" / "claude-md-transcripts" / "qmd"
    )
```

Make sure to merge the new imports into the existing `from claude_md_transcripts.paths import (...)` block at the top of the file rather than adding a duplicate block.

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `uv run pytest tests/test_paths.py -v`
Expected: the four new tests fail with `ImportError` (helpers don't exist yet). Existing tests still pass.

- [ ] **Step 3: Implement the helpers in `paths.py`**

Replace the body of `claude_md_transcripts/paths.py` so it ends with these helpers (keep the existing `claude_projects_dir`, `encode_host_path`, `resolve_session_dir`, and `output_dir_for_collection` functions in place — they are still used by callers we haven't touched yet):

```python
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
```

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest tests/test_paths.py -v`
Expected: all tests pass.

Run: `make check`
Expected: lint, typecheck, and tests all green.

- [ ] **Step 5: Commit**

```bash
git add claude_md_transcripts/paths.py tests/test_paths.py
git commit -m "Add default_output_root / default_subdir_name / default_output_dir_for helpers"
```

---

## Task 2: Strip qmd integration from `SyncOrchestrator`

This task removes all qmd subprocess calls from `SyncOrchestrator` and from `cli.py:_make_orchestrator`. Public method signatures on `SyncOrchestrator` (`sync_session_dir(..., collection=, description=)`, `retitle_collection(collection, ...)`) stay the same in this task so that `cli.py` continues to compile. Renaming those parameters to `output_dir` happens in Task 3.

**Files:**
- Modify: `claude_md_transcripts/sync.py`
- Modify: `claude_md_transcripts/cli.py`
- Modify: `tests/test_sync.py`
- Modify: `tests/test_retitle.py`

- [ ] **Step 1: Update `tests/test_sync.py` to expect a qmd-free orchestrator**

Replace the entire content of `tests/test_sync.py` with:

```python
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_md_transcripts.render import RenderConfig
from claude_md_transcripts.sync import SyncOrchestrator, default_collection_name


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
    return tmp_path / "claude-md-transcripts" / "test-collection"


@pytest.fixture
def orchestrator(output_dir: Path) -> SyncOrchestrator:
    return SyncOrchestrator(
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
    result = orchestrator.sync_session_dir(session_dir, collection="test-collection")
    assert result.files_total == 2
    assert result.files_converted == 2
    md_files = sorted(output_dir.glob("*.md"))
    assert len(md_files) == 2


def test_sync_creates_output_dir(
    orchestrator: SyncOrchestrator, session_dir: Path, output_dir: Path
):
    assert not output_dir.exists()
    orchestrator.sync_session_dir(session_dir, collection="test-collection")
    assert output_dir.exists()


def test_sync_idempotent_when_input_unchanged(session_dir: Path, output_dir: Path):
    """Re-running sync without input changes does not re-render."""
    orch1 = SyncOrchestrator(render_config=RenderConfig(), output_root=output_dir.parent)
    r1 = orch1.sync_session_dir(session_dir, collection="test-collection")

    md_files = list(output_dir.glob("*.md"))
    initial_mtimes = {p: p.stat().st_mtime_ns for p in md_files}

    orch2 = SyncOrchestrator(render_config=RenderConfig(), output_root=output_dir.parent)
    r2 = orch2.sync_session_dir(session_dir, collection="test-collection")

    final_mtimes = {p: p.stat().st_mtime_ns for p in output_dir.glob("*.md")}
    assert initial_mtimes == final_mtimes
    assert r1.files_converted == 2
    assert r2.files_converted == 0
    assert r2.files_unchanged == 2


def test_sync_re_renders_when_input_newer(session_dir: Path, output_dir: Path):
    orch = SyncOrchestrator(render_config=RenderConfig(), output_root=output_dir.parent)
    orch.sync_session_dir(session_dir, collection="test-collection")
    md_files = sorted(output_dir.glob("*.md"))
    assert md_files

    target = next(session_dir.glob("11111111*.jsonl"))
    new_mtime = target.stat().st_mtime + 100
    os.utime(target, (new_mtime, new_mtime))

    r = orch.sync_session_dir(session_dir, collection="test-collection")
    assert r.files_converted == 1
    assert r.files_unchanged == 1


def test_sync_skips_oversized_file(session_dir: Path, output_dir: Path):
    big = session_dir / "33333333-3333-3333-3333-333333333333.jsonl"
    big.write_bytes(b"x" * 4096)  # not valid json, but the size guard fires first
    orch = SyncOrchestrator(
        render_config=RenderConfig(),
        output_root=output_dir.parent,
        max_bytes=2048,
    )
    r = orch.sync_session_dir(session_dir, collection="test-collection")
    assert r.files_skipped_for_size == 1
    assert r.files_converted == 2
```

Notes for the engineer:
- Three tests from the old file are intentionally gone: `test_sync_calls_qmd_lifecycle`, `test_sync_does_not_re_add_existing_collection`, `test_sync_skips_context_when_description_omitted`. Their behaviors no longer exist.
- `description=` is dropped from every remaining call. The CLI side keeps the flag for now and threads it through, but the orchestrator stops using it.

- [ ] **Step 2: Update `tests/test_retitle.py` to expect a qmd-free orchestrator**

Apply these edits to `tests/test_retitle.py`:

1. Remove the qmd imports near the top:

```python
from claude_md_transcripts.qmd import QmdClient
from tests.test_qmd import FakeResult, FakeRunner
```

2. Replace `make_orch` so it no longer threads a qmd runner:

```python
def make_orch(
    output_root: Path,
    smart_response: FakeRun | Exception | None = None,
) -> SyncOrchestrator:
    smart_runner = ClaudeFakeRunner(
        smart_response or FakeRun(stdout="Investigate slow user query\n")
    )
    smart_gen = SmartSlugGenerator(runner=smart_runner)
    return SyncOrchestrator(
        render_config=RenderConfig(),
        output_root=output_root,
        smart_slug_generator=smart_gen,
    )
```

3. Update every `make_orch(output_root, qmd_responses=..., smart_response=...)` call site to drop the `qmd_responses=` argument. The remaining call shape is `make_orch(output_root, smart_response=FakeRun(...))` (or just `make_orch(output_root)`).

4. Replace every other in-file `SyncOrchestrator(qmd=QmdClient(runner=FakeRunner(...)), ...)` construction with the qmd-free form, e.g.:

```python
plain = SyncOrchestrator(
    render_config=RenderConfig(),
    output_root=output_root,
)
```

5. Drop the `description="x"` keyword from every `sync_session_dir` call. The remaining shape is `sync_session_dir(session_dir, collection="coll1")`.

6. Delete the entire test `test_retitle_runs_qmd_update_only_on_changes` (the behavior it asserts on no longer exists).

7. In `test_retitle_requires_smart_generator` and `test_retitle_returns_empty_for_missing_collection`, drop the `qmd=...` argument from the `SyncOrchestrator(...)` constructor.

- [ ] **Step 3: Run the updated tests against the still-qmd-shaped orchestrator and confirm they fail**

Run: `uv run pytest tests/test_sync.py tests/test_retitle.py -v`
Expected: many failures of the form `TypeError: __init__() missing 1 required keyword-only argument: 'qmd'` or import errors. This confirms the tests are now driving the qmd removal in `sync.py`.

- [ ] **Step 4: Strip qmd from `claude_md_transcripts/sync.py`**

Apply these edits in order.

1. Replace the module docstring at the top of `sync.py` with:

```python
"""
Orchestrate end-to-end conversion of a session directory into a markdown collection.

The orchestrator wires together :mod:`reader`, :mod:`render`, :mod:`slug`,
and :mod:`paths`. It is constructed by injection so callers (CLI, tests,
future schedulers) can swap out the render config or smart-slug generator
without touching this module.
"""
```

2. Remove the `from .qmd import QmdClient` import.

3. Replace `SyncOrchestrator.__init__` so it no longer takes a `qmd` parameter:

```python
def __init__(
    self,
    *,
    render_config: RenderConfig,
    output_root: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    smart_slug_generator: SmartSlugGenerator | None = None,
) -> None:
    self.render_config = render_config
    self.output_root = output_root
    self.max_bytes = max_bytes
    self.smart_slug_generator = smart_slug_generator
```

4. Replace the body of `sync_session_dir` so it no longer calls qmd. The new body keeps the per-file conversion logic but drops every `self.qmd.*` call and the `description` parameter handling:

```python
def sync_session_dir(
    self,
    session_dir: Path,
    *,
    collection: str | None = None,
    description: str | None = None,
) -> SyncResult:
    """
    Convert all sessions in ``session_dir`` and write markdown to the output dir.

    The ``description`` parameter is accepted but ignored; it is kept for
    one task only so the existing CLI keeps compiling, and is removed in
    the next task along with the rest of the legacy flag surface.
    """
    coll_name = collection or default_collection_name(session_dir)
    out_dir = self._output_dir_for(coll_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = SyncResult(
        project_path=None,
        session_dir=session_dir,
        collection=coll_name,
        output_dir=out_dir,
    )

    jsonl_paths = sorted(session_dir.glob("*.jsonl"))
    logger.info(
        "sync: scanning %s -> %s (collection=%s, %d session files)",
        session_dir,
        out_dir,
        coll_name,
        len(jsonl_paths),
    )
    for i, jsonl_path in enumerate(jsonl_paths, 1):
        summary.files_total += 1
        self._convert_one(jsonl_path, out_dir, summary, index=i, total=len(jsonl_paths))

    logger.info(
        "sync: done %s (converted=%d, unchanged=%d, skipped_for_size=%d, skipped_empty=%d)",
        coll_name,
        summary.files_converted,
        summary.files_unchanged,
        summary.files_skipped_for_size,
        summary.files_skipped_empty,
    )
    return summary
```

5. Replace `retitle_collection` so it no longer calls `self.qmd.update()`:

```python
def retitle_collection(
    self,
    collection: str,
    *,
    force: bool = False,
) -> RetitleResult:
    """
    Apply smart titles to markdown files in an existing collection.

    Walks ``output_dir / collection / *.md`` and, for each file that
    does not already carry ``smart_title: true`` in its frontmatter
    (or every file if ``force`` is set), runs the smart-slug generator
    on the existing markdown body, updates the frontmatter, and renames
    the file when the resulting slug differs.
    """
    if self.smart_slug_generator is None:
        raise ValueError("retitle_collection requires a smart_slug_generator")
    out_dir = self._output_dir_for(collection)
    result = RetitleResult(collection=collection, output_dir=out_dir)
    if not out_dir.exists():
        logger.info("retitle: no output directory at %s, nothing to do", out_dir)
        return result
    md_paths = sorted(out_dir.glob("*.md"))
    logger.info(
        "retitle: scanning %s (%d markdown files, force=%s)",
        out_dir,
        len(md_paths),
        force,
    )
    for i, md_path in enumerate(md_paths, 1):
        result.files_total += 1
        outcome = self._retitle_one(md_path, force=force, index=i, total=len(md_paths))
        if outcome == "retitled":
            result.files_retitled += 1
        elif outcome == "already_smart":
            result.files_skipped_already_smart += 1
        elif outcome == "failed":
            result.files_skipped_failed += 1
    logger.info(
        "retitle: done %s (retitled=%d, already_smart=%d, failed=%d)",
        collection,
        result.files_retitled,
        result.files_skipped_already_smart,
        result.files_skipped_failed,
    )
    return result
```

6. Leave `default_collection_name`, `_output_dir_for`, `_convert_one`, `_existing_output_for`, `_pick_slug_with_source`, `_build_filename`, `_retitle_one`, and `_renamed_path_for` exactly as they are. They will move or be renamed in Task 3.

- [ ] **Step 5: Strip qmd from `claude_md_transcripts/cli.py:_make_orchestrator`**

In `claude_md_transcripts/cli.py`:

1. Remove the imports `from .qmd import QmdClient` and `import os` (the latter only if no other use of `os` remains in the file — leave it if it does).

2. Replace `_make_orchestrator` with:

```python
def _make_orchestrator(
    *, include_thinking: bool, max_bytes: int, smart_titles: bool = False
) -> SyncOrchestrator:
    """
    Construct a default orchestrator wired for direct markdown export.
    """
    smart_gen = SmartSlugGenerator() if smart_titles else None
    return SyncOrchestrator(
        render_config=RenderConfig(include_thinking=include_thinking),
        max_bytes=max_bytes,
        smart_slug_generator=smart_gen,
    )
```

3. Update the `cli` group docstring (the function `def cli(verbose, quiet)`) so it reads:

```python
"""
Convert Claude Code session JSONL transcripts to markdown collections.
"""
```

- [ ] **Step 6: Run the test suite**

Run: `uv run pytest tests/test_sync.py tests/test_retitle.py -v`
Expected: all tests pass.

Run: `uv run pytest tests/test_qmd.py tests/test_cli.py -v`
Expected: `test_qmd.py` still passes (the module is still on disk; it gets removed in Task 4). `test_cli.py` may have one or two failures because it sets `CLAUDE_MD_TRANSCRIPTS_QMD_BIN` and we no longer read it; if `test_cli.py` still passes, great — its assertions only look at output text and on-disk files, not at the env var. **If a test fails here, do not patch `cli.py` to fix it; the env-var read has been removed intentionally and `test_cli.py` is rewritten in Task 3.** Note the exact failures and continue.

Run: `make check`
Expected: green if `test_cli.py` happens to still pass, otherwise lint + typecheck pass and only `test_cli.py` fails. If anything else fails, stop and investigate.

- [ ] **Step 7: Commit**

```bash
git add claude_md_transcripts/sync.py claude_md_transcripts/cli.py \
    tests/test_sync.py tests/test_retitle.py
git commit -m "Drop qmd integration from SyncOrchestrator and CLI orchestrator factory"
```

---

## Task 3: Rename CLI commands and switch to `--output-dir`

This task replaces `--collection` / `--description` with `--output-dir`, renames `sync` / `sync-all` to `export` / `export-all`, and drops the `description` parameter from `SyncOrchestrator.sync_session_dir`. After this task `default_collection_name` still lives in `sync.py`; it moves to `paths.py` in Task 4 along with the legacy `output_dir_for_collection` removal.

**Files:**
- Modify: `claude_md_transcripts/cli.py`
- Modify: `claude_md_transcripts/sync.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_sync.py`
- Modify: `tests/test_retitle.py`

- [ ] **Step 1: Rewrite `tests/test_cli.py` to drive the new flag and command surface**

Replace the entire content of `tests/test_cli.py` with:

```python
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
    encoded_for_host = "-" + str(host_path).replace("/", "-").replace(".", "-").lstrip("-")
    (proj_dir / encoded_for_host).symlink_to(d)

    runner = CliRunner()
    result = runner.invoke(cli, ["export", str(host_path)])
    assert result.exit_code == 0, result.output

    expected_out = fake_home / ".claude" / "claude-md-transcripts" / "foo"
    assert expected_out.exists()
    assert any(expected_out.glob("*.md"))


def test_export_all_iterates_session_dirs(tmp_path: Path, monkeypatch):
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

    runner = CliRunner()
    result = runner.invoke(cli, ["export-all"])
    assert result.exit_code == 0, result.output
    assert "foo" in result.output
    assert "bar" in result.output

    out_root = fake_home / ".claude" / "claude-md-transcripts"
    assert (out_root / "foo").exists()
    assert (out_root / "bar").exists()


def test_export_all_with_explicit_output_root(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    proj_dir = fake_home / ".claude" / "projects"
    proj_dir.mkdir(parents=True)
    d = proj_dir / "-Users-fake-projects-foo"
    d.mkdir()
    _write_minimal_session(d / "aaaaaaaa-1111-1111-1111-111111111111.jsonl")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    custom_root = tmp_path / "custom"

    runner = CliRunner()
    result = runner.invoke(cli, ["export-all", "--output-dir", str(custom_root)])
    assert result.exit_code == 0, result.output

    assert (custom_root / "foo").exists()
    assert any((custom_root / "foo").glob("*.md"))


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
    for name, sid in (
        ("-Users-fake-projects-foo", "aaaaaaaa-1111-1111-1111-111111111111"),
        ("-Users-fake-projects-bar", "bbbbbbbb-2222-2222-2222-222222222222"),
    ):
        d = proj_dir / name
        d.mkdir()
        _write_minimal_session(d / f"{sid}.jsonl", session_id=sid)

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
    assert (out_root / "foo").exists()
    assert (out_root / "bar").exists()
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
```

Notes:
- `CLAUDE_MD_TRANSCRIPTS_QMD_BIN`, fake-qmd shell scripts, and `--description` are gone.
- `test_export_with_host_path_uses_default_output_dir` relies on `paths.encode_host_path` matching the symlinked encoded directory; it constructs `encoded_for_host` the same way `encode_host_path` does.

- [ ] **Step 2: Update `tests/test_sync.py` and `tests/test_retitle.py` for the orchestrator parameter rename**

In `tests/test_sync.py`, replace every `orchestrator.sync_session_dir(session_dir, collection="test-collection")` with `orchestrator.sync_session_dir(session_dir, output_dir=output_dir)`. Update the `output_dir` fixture to actually be the *full* output directory used by tests (it already is: `tmp_path / "claude-md-transcripts" / "test-collection"`).

After the edit, the fixture-using tests look like:

```python
def test_sync_writes_one_markdown_per_jsonl(
    orchestrator: SyncOrchestrator, session_dir: Path, output_dir: Path
):
    result = orchestrator.sync_session_dir(session_dir, output_dir=output_dir)
    ...
```

…and so on for `test_sync_creates_output_dir`, `test_sync_idempotent_when_input_unchanged`, `test_sync_re_renders_when_input_newer`, `test_sync_skips_oversized_file`. Also drop the `output_root=output_dir.parent` argument from every `SyncOrchestrator(...)` construction in this file (it's no longer needed because tests pass `output_dir=` directly). The fixture `orchestrator` becomes:

```python
@pytest.fixture
def orchestrator() -> SyncOrchestrator:
    return SyncOrchestrator(render_config=RenderConfig())
```

Likewise drop `output_root=...` from the per-test orchestrator constructions in `test_sync_idempotent_when_input_unchanged`, `test_sync_re_renders_when_input_newer`, and `test_sync_skips_oversized_file`.

In `tests/test_retitle.py`:

1. Drop the `output_root` argument from `make_orch` and from every direct `SyncOrchestrator(...)` construction.
2. Replace every `sync_session_dir(session_dir, collection="coll1")` with `sync_session_dir(session_dir, output_dir=output_root / "coll1")`.
3. Replace every `retitle_collection("coll1")` with `retitle_collection(output_root / "coll1")`. The `retitle_collection` API now takes an output directory `Path`.
4. The `output_root` fixture stays as-is (`tmp_path / "qmd-transcripts"` is fine, just a temp dir name).

- [ ] **Step 3: Run the rewritten tests against current code and watch them fail**

Run: `uv run pytest tests/test_cli.py tests/test_sync.py tests/test_retitle.py -v`
Expected: failures along the lines of `Got unexpected keyword argument 'output_dir'`, `No such command 'export'`, `unrecognized arguments: --output-dir`. This confirms the tests are now driving the CLI surface change in `cli.py` / `sync.py`.

- [ ] **Step 4: Update `claude_md_transcripts/sync.py` for the parameter rename**

Apply these changes:

1. Replace `sync_host_project` so it forwards an output dir:

```python
def sync_host_project(
    self,
    host_path: Path,
    *,
    output_dir: Path | None = None,
) -> SyncResult:
    """
    Sync a host project by resolving its Claude Code session directory.
    """
    session_dir = resolve_session_dir(host_path)
    result = self.sync_session_dir(session_dir, output_dir=output_dir)
    result.project_path = host_path.resolve()
    return result
```

2. Replace `sync_session_dir` body:

```python
def sync_session_dir(
    self,
    session_dir: Path,
    *,
    output_dir: Path | None = None,
) -> SyncResult:
    """
    Convert all sessions in ``session_dir`` and write markdown to ``output_dir``.

    If ``output_dir`` is None, the destination is derived from the session
    directory's basename via :func:`default_output_dir_for`.
    """
    out_dir = output_dir if output_dir is not None else default_output_dir_for(session_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = SyncResult(
        project_path=None,
        session_dir=session_dir,
        collection=out_dir.name,
        output_dir=out_dir,
    )

    jsonl_paths = sorted(session_dir.glob("*.jsonl"))
    logger.info(
        "sync: scanning %s -> %s (%d session files)",
        session_dir,
        out_dir,
        len(jsonl_paths),
    )
    for i, jsonl_path in enumerate(jsonl_paths, 1):
        summary.files_total += 1
        self._convert_one(jsonl_path, out_dir, summary, index=i, total=len(jsonl_paths))

    logger.info(
        "sync: done %s (converted=%d, unchanged=%d, skipped_for_size=%d, skipped_empty=%d)",
        out_dir,
        summary.files_converted,
        summary.files_unchanged,
        summary.files_skipped_for_size,
        summary.files_skipped_empty,
    )
    return summary
```

3. Replace `retitle_collection` body so it takes an output dir directly:

```python
def retitle_collection(
    self,
    output_dir: Path,
    *,
    force: bool = False,
) -> RetitleResult:
    """
    Apply smart titles to markdown files in an existing output directory.

    Walks ``output_dir / *.md`` and, for each file that does not already
    carry ``smart_title: true`` in its frontmatter (or every file if
    ``force`` is set), runs the smart-slug generator on the existing
    markdown body, updates the frontmatter, and renames the file when
    the resulting slug differs.
    """
    if self.smart_slug_generator is None:
        raise ValueError("retitle_collection requires a smart_slug_generator")
    result = RetitleResult(collection=output_dir.name, output_dir=output_dir)
    if not output_dir.exists():
        logger.info("retitle: no output directory at %s, nothing to do", output_dir)
        return result
    md_paths = sorted(output_dir.glob("*.md"))
    logger.info(
        "retitle: scanning %s (%d markdown files, force=%s)",
        output_dir,
        len(md_paths),
        force,
    )
    for i, md_path in enumerate(md_paths, 1):
        result.files_total += 1
        outcome = self._retitle_one(md_path, force=force, index=i, total=len(md_paths))
        if outcome == "retitled":
            result.files_retitled += 1
        elif outcome == "already_smart":
            result.files_skipped_already_smart += 1
        elif outcome == "failed":
            result.files_skipped_failed += 1
    logger.info(
        "retitle: done %s (retitled=%d, already_smart=%d, failed=%d)",
        output_dir,
        result.files_retitled,
        result.files_skipped_already_smart,
        result.files_skipped_failed,
    )
    return result
```

4. Replace `_output_dir_for` with a no-op or remove it entirely — the new code uses `default_output_dir_for` from `paths.py` directly. Delete the method definition and the `output_root: Path | None = None` constructor parameter:

```python
def __init__(
    self,
    *,
    render_config: RenderConfig,
    max_bytes: int = DEFAULT_MAX_BYTES,
    smart_slug_generator: SmartSlugGenerator | None = None,
) -> None:
    self.render_config = render_config
    self.max_bytes = max_bytes
    self.smart_slug_generator = smart_slug_generator
```

5. Update the import block at the top of `sync.py` to use the new helpers:

```python
from .paths import default_output_dir_for, resolve_session_dir
```

(Remove the old `output_dir_for_collection` import.)

6. Leave `default_collection_name` and `SyncResult.collection` in place for now. They're tidied up in Task 4.

- [ ] **Step 5: Update `claude_md_transcripts/cli.py` for the new commands and flags**

Replace the entire body of `cli.py` with:

```python
"""
Command-line entry point for ``claude-md-transcripts``.

Each subcommand wires up the existing :class:`SyncOrchestrator` (or the
reader directly for ``inspect``) and prints a short summary.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path

import click

from .discovery import ProjectInfo, discover_projects
from .paths import (
    claude_projects_dir,
    default_output_dir_for,
    default_output_root,
    resolve_session_dir,
)
from .picker import is_tty, pick_projects
from .reader import DEFAULT_MAX_BYTES, read_session
from .render import RenderConfig
from .smart_slug import SmartSlugGenerator
from .sync import SyncOrchestrator, SyncResult


def _make_orchestrator(
    *, include_thinking: bool, max_bytes: int, smart_titles: bool = False
) -> SyncOrchestrator:
    """
    Construct a default orchestrator wired for direct markdown export.
    """
    smart_gen = SmartSlugGenerator() if smart_titles else None
    return SyncOrchestrator(
        render_config=RenderConfig(include_thinking=include_thinking),
        max_bytes=max_bytes,
        smart_slug_generator=smart_gen,
    )


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose (DEBUG) logging.")
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress per-file progress; only show warnings and errors.",
)
def cli(verbose: bool, quiet: bool) -> None:
    """
    Convert Claude Code session JSONL transcripts to markdown collections.
    """
    if verbose and quiet:
        raise click.UsageError("--verbose and --quiet cannot be combined.")
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


@cli.command()
@click.argument("host_path", required=False, type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--session-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Operate on this Claude Code session directory directly (skips host-path resolution).",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where to write markdown for this run "
    "(default: ~/.claude/claude-md-transcripts/<basename>/).",
)
@click.option("--include-thinking", is_flag=True, help="Include assistant 'thinking' blocks.")
@click.option(
    "--smart-titles",
    is_flag=True,
    help="Generate session titles inline by asking 'claude -p' to summarize.",
)
@click.option(
    "--max-bytes",
    type=int,
    default=DEFAULT_MAX_BYTES,
    show_default=True,
    help="Skip session files larger than this many bytes.",
)
def export(
    host_path: Path | None,
    session_dir: Path | None,
    output_dir: Path | None,
    include_thinking: bool,
    smart_titles: bool,
    max_bytes: int,
) -> None:
    """
    Convert a host project's Claude Code sessions to markdown.

    With no positional path or ``--session-dir``, drops into an interactive
    multi-select for projects discovered under ``~/.claude/projects/``.
    """
    orch = _make_orchestrator(
        include_thinking=include_thinking,
        max_bytes=max_bytes,
        smart_titles=smart_titles,
    )

    if host_path is None and session_dir is None:
        if output_dir is not None:
            raise click.UsageError(
                "--output-dir cannot be combined with interactive mode "
                "(it would apply to every selected project). "
                "Pass HOST_PATH or --session-dir to use --output-dir."
            )
        if not is_tty():
            raise click.UsageError(
                "Provide HOST_PATH or --session-dir, or run interactively in a terminal."
            )
        _run_interactive_export(orch)
        return

    if session_dir is not None:
        result = orch.sync_session_dir(session_dir, output_dir=output_dir)
    else:
        assert host_path is not None
        try:
            result = orch.sync_host_project(host_path, output_dir=output_dir)
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e

    _print_export_summary(result)


def _run_interactive_export(orch: SyncOrchestrator) -> None:
    """
    Discover projects, prompt the user to pick a subset, and export each one.
    """
    projects = discover_projects(claude_projects_dir())
    if not projects:
        click.echo(f"No project directories found under {claude_projects_dir()}.")
        return

    selected = pick_projects(projects)
    if selected is None:
        click.echo("Cancelled.")
        return
    if not selected:
        click.echo("Nothing selected.")
        return

    results: list[SyncResult] = []
    for info in selected:
        out_dir = default_output_dir_for(info.session_dir)
        click.echo(f"\n=== {info.basename} ({out_dir}) ===")
        result = orch.sync_session_dir(info.session_dir, output_dir=out_dir)
        results.append(result)
        _print_export_summary(result, indent="  ")

    if len(results) > 1:
        totals = {
            "files_total": sum(r.files_total for r in results),
            "files_converted": sum(r.files_converted for r in results),
            "files_unchanged": sum(r.files_unchanged for r in results),
            "files_skipped_for_size": sum(r.files_skipped_for_size for r in results),
        }
        click.echo(
            "\nTotals across {n} projects: total={t} converted={c} unchanged={u} "
            "skipped_for_size={s}".format(
                n=len(results),
                t=totals["files_total"],
                c=totals["files_converted"],
                u=totals["files_unchanged"],
                s=totals["files_skipped_for_size"],
            )
        )


@cli.command("export-all")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Output root for all projects (default: ~/.claude/claude-md-transcripts/).",
)
@click.option("--include-thinking", is_flag=True, help="Include assistant 'thinking' blocks.")
@click.option(
    "--smart-titles",
    is_flag=True,
    help="Generate session titles inline by asking 'claude -p' to summarize.",
)
@click.option(
    "--max-bytes",
    type=int,
    default=DEFAULT_MAX_BYTES,
    show_default=True,
    help="Skip session files larger than this many bytes.",
)
def export_all(
    output_dir: Path | None,
    include_thinking: bool,
    smart_titles: bool,
    max_bytes: int,
) -> None:
    """
    Convert every Claude Code project directory under ~/.claude/projects/.
    """
    orch = _make_orchestrator(
        include_thinking=include_thinking,
        max_bytes=max_bytes,
        smart_titles=smart_titles,
    )
    root = claude_projects_dir()
    if not root.exists():
        raise click.ClickException(f"No Claude Code projects directory at {root}")
    project_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not project_dirs:
        click.echo("No project directories found.")
        return
    output_root = output_dir if output_dir is not None else default_output_root()
    total: collections.Counter[str] = collections.Counter()
    for d in project_dirs:
        out_dir = output_root / _subdir_for(d)
        click.echo(f"\n→ {d.name}  ({out_dir})")
        result = orch.sync_session_dir(d, output_dir=out_dir)
        _print_export_summary(result, indent="  ")
        total["files_total"] += result.files_total
        total["files_converted"] += result.files_converted
        total["files_unchanged"] += result.files_unchanged
        total["files_skipped_for_size"] += result.files_skipped_for_size
    click.echo(
        f"\nTotals: total={total['files_total']} "
        f"converted={total['files_converted']} "
        f"unchanged={total['files_unchanged']} "
        f"skipped_for_size={total['files_skipped_for_size']}"
    )


def _subdir_for(session_dir: Path) -> str:
    """
    Compute the per-project subdir name for export-all.
    """
    from .paths import default_subdir_name

    return default_subdir_name(session_dir)


@cli.command()
@click.argument("host_path", required=False, type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Output directory to retitle (default: derived from HOST_PATH).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Retitle even files that already carry smart_title: true in their frontmatter.",
)
def retitle(host_path: Path | None, output_dir: Path | None, force: bool) -> None:
    """
    Apply smart titles to a previously-exported output directory.
    """
    if output_dir is None:
        if host_path is None:
            raise click.UsageError("Provide either HOST_PATH or --output-dir.")
        try:
            session_dir = resolve_session_dir(host_path)
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e
        output_dir = default_output_dir_for(session_dir)
    orch = _make_orchestrator(
        include_thinking=False,
        max_bytes=DEFAULT_MAX_BYTES,
        smart_titles=True,
    )
    result = orch.retitle_collection(output_dir, force=force)
    _print_retitle_summary(result)


@cli.command("retitle-all")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Output root to walk for subdirs to retitle "
    "(default: ~/.claude/claude-md-transcripts/).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Retitle even files that already carry smart_title: true in their frontmatter.",
)
def retitle_all(output_dir: Path | None, force: bool) -> None:
    """
    Apply smart titles to every output directory under the export root.
    """
    orch = _make_orchestrator(
        include_thinking=False,
        max_bytes=DEFAULT_MAX_BYTES,
        smart_titles=True,
    )
    root = output_dir if output_dir is not None else default_output_root()
    if not root.exists():
        click.echo(f"No transcripts root at {root}.")
        return
    coll_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not coll_dirs:
        click.echo("No subdirectories found.")
        return
    totals: collections.Counter[str] = collections.Counter()
    for d in coll_dirs:
        click.echo(f"\n→ {d.name}")
        result = orch.retitle_collection(d, force=force)
        _print_retitle_summary(result, indent="  ")
        totals["files_total"] += result.files_total
        totals["files_retitled"] += result.files_retitled
        totals["files_skipped_already_smart"] += result.files_skipped_already_smart
        totals["files_skipped_failed"] += result.files_skipped_failed
    click.echo(
        f"\nTotals: total={totals['files_total']} "
        f"retitled={totals['files_retitled']} "
        f"already_smart={totals['files_skipped_already_smart']} "
        f"failed={totals['files_skipped_failed']}"
    )


@cli.command()
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def inspect(source: Path) -> None:
    """
    Print a quick summary of a Claude Code session JSONL file.
    """
    result = read_session(source)
    counter: collections.Counter[str] = collections.Counter()
    for r in result.records:
        counter[type(r.parsed).__name__] += 1
    kept = sum(1 for r in result.iter_kept())
    click.echo(f"path:          {result.path}")
    click.echo(f"size_bytes:    {result.size_bytes}")
    click.echo(f"session_id:    {result.session_id}")
    click.echo(f"custom_title:  {result.custom_title or '(none)'}")
    click.echo(f"records:       {len(result.records)}")
    click.echo(f"kept:          {kept}")
    click.echo(f"skipped:       {len(result.records) - kept}")
    click.echo(f"parse_errors:  {result.parse_errors}")
    click.echo("by type:")
    for k, v in counter.most_common():
        click.echo(f"  {k}: {v}")


def _print_export_summary(result, indent: str = "") -> None:
    """
    Render a SyncResult to stdout in a one-glance format.
    """
    click.echo(
        f"{indent}out={result.output_dir} "
        f"total={result.files_total} "
        f"converted={result.files_converted} "
        f"unchanged={result.files_unchanged} "
        f"skipped_for_size={result.files_skipped_for_size} "
        f"skipped_empty={result.files_skipped_empty}"
    )


def _print_retitle_summary(result, indent: str = "") -> None:
    """
    Render a RetitleResult to stdout.
    """
    click.echo(
        f"{indent}out={result.output_dir} "
        f"total={result.files_total} "
        f"retitled={result.files_retitled} "
        f"already_smart={result.files_skipped_already_smart} "
        f"failed={result.files_skipped_failed}"
    )


def main() -> None:
    """
    Entry point used by the ``claude-md-transcripts`` console script.
    """
    cli()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -v`
Expected: every test passes except `tests/test_qmd.py` (which still passes — it tests the still-on-disk `qmd.py` module). Pay special attention to `test_export_all_iterates_session_dirs` and `test_export_with_host_path_uses_default_output_dir`; both exercise the new default-output-dir derivation.

Run: `make check`
Expected: lint, typecheck, and tests all green.

- [ ] **Step 7: Commit**

```bash
git add claude_md_transcripts/cli.py claude_md_transcripts/sync.py \
    tests/test_cli.py tests/test_sync.py tests/test_retitle.py
git commit -m "Rename sync/sync-all to export/export-all and replace --collection/--description with --output-dir"
```

---

## Task 4: Delete `qmd.py` and `test_qmd.py`; relocate name helpers

This is pure cleanup. Nothing references `QmdClient`, `QmdError`, or `output_dir_for_collection` anymore.

**Files:**
- Delete: `claude_md_transcripts/qmd.py`
- Delete: `tests/test_qmd.py`
- Modify: `claude_md_transcripts/paths.py`
- Modify: `claude_md_transcripts/sync.py`
- Modify: `tests/test_paths.py`
- Modify: `tests/test_sync.py`

- [ ] **Step 1: Verify nothing imports `qmd` anymore**

Run: `grep -RIn "from claude_md_transcripts.qmd\|claude_md_transcripts\.qmd\|QmdClient\|QmdError" claude_md_transcripts tests`
Expected: only `claude_md_transcripts/qmd.py` and `tests/test_qmd.py` show matches.

If anything else matches, stop and clean up the leftover import before continuing.

- [ ] **Step 2: Delete the qmd files**

Run: `git rm claude_md_transcripts/qmd.py tests/test_qmd.py`
Expected: both files are removed and staged for deletion.

- [ ] **Step 3: Remove `output_dir_for_collection` from `paths.py` and its test**

Edit `claude_md_transcripts/paths.py`: delete the `output_dir_for_collection` function definition entirely (the function should be gone; everything else stays).

Edit `tests/test_paths.py`: remove the `output_dir_for_collection` import and the test `test_output_dir_for_collection`.

- [ ] **Step 4: Move `default_collection_name` from `sync.py` to `paths.py` (renaming retained for backwards-compat in tests)**

Wait — re-check whether anything outside `tests/test_sync.py` still references `default_collection_name`:

Run: `grep -RIn "default_collection_name" claude_md_transcripts tests`
Expected: only `claude_md_transcripts/sync.py` (definition + import) and `tests/test_sync.py` (two tests + import).

Plan: keep `default_collection_name` as a thin shim that re-exports the legacy name shape `<basename>-claude-sessions` for the two existing tests, OR delete it. Per the spec, delete it.

Apply these edits:

In `claude_md_transcripts/sync.py`:
- Delete the `default_collection_name` function entirely.
- Keep `SyncResult.collection` as a field (the orchestrator now sets it to `out_dir.name`, which is fine), but remove the `default_collection_name` import path that any caller relied on.

In `tests/test_sync.py`:
- Remove the import of `default_collection_name`.
- Delete the tests `test_default_collection_name_derived_from_session_dir` and `test_default_collection_name_handles_short_paths`. Their replacement coverage already lives in `tests/test_paths.py` (Task 1's `test_default_subdir_name_*` cases).

- [ ] **Step 5: Drop `RetitleResult.collection` field**

The spec says `RetitleResult.collection` is redundant with `output_dir`. Edit `claude_md_transcripts/sync.py`:

```python
@dataclass
class RetitleResult:
    """
    Summary of a single retitle pass.
    """

    output_dir: Path
    files_total: int = 0
    files_retitled: int = 0
    files_skipped_already_smart: int = 0
    files_skipped_failed: int = 0
    renamed_paths: list[tuple[Path, Path]] = field(default_factory=list)
```

Update the `retitle_collection` body so the result construction drops `collection=`:

```python
result = RetitleResult(output_dir=output_dir)
```

`SyncResult.collection` stays as-is — it's still populated (via `out_dir.name`) and the spec doesn't ask for its removal.

- [ ] **Step 6: Verify no test references the removed `RetitleResult.collection` field**

Run: `grep -RIn "RetitleResult(.*collection=\|RetitleResult\\.collection" tests claude_md_transcripts`
Expected: empty output. If anything matches, drop the `collection=` kwarg or update the access.

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -v`
Expected: every test passes. The total file count drops by one (qmd test file gone).

Run: `make check`
Expected: green.

- [ ] **Step 8: Commit**

```bash
git add -u claude_md_transcripts/qmd.py claude_md_transcripts/paths.py \
    claude_md_transcripts/sync.py tests/test_qmd.py tests/test_paths.py tests/test_sync.py
git commit -m "Delete qmd module and legacy collection-name helpers"
```

(`git add -u` stages the deletions of removed files alongside edits to surviving ones.)

---

## Task 5: Update docs and bump version

Last task. No production code changes; only README, CLAUDE.md, module docstrings, and `pyproject.toml`.

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `claude_md_transcripts/sync.py` (module docstring already updated in Task 2 — verify)
- Modify: `claude_md_transcripts/paths.py` (module docstring)
- Modify: `claude_md_transcripts/render.py` (module docstring mentions qmd)
- Modify: `pyproject.toml`

- [ ] **Step 1: Rewrite `README.md`**

Replace the entire content of `README.md` with:

````markdown
# claude-md-transcripts

Convert your Claude Code session JSONL transcripts into clean markdown files, organized one directory per project, ready to feed into any indexer or search tool.

## Why

Claude Code records every session as a JSONL file under `~/.claude/projects/<encoded-project-path>/<session-id>.jsonl`. Those files are dense and hard to search:

- Tool I/O is bulky (long bash output, file reads, MCP responses)
- Image attachments are stored as base64, so a single Playwright screenshot can blow a session up to hundreds of megabytes
- Per-line metadata (`sessionId`, `cwd`, `gitBranch`, `version`, etc.) repeats on almost every line
- Auxiliary line types like `file-history-snapshot`, `permission-mode`, and `attachment` carry no conversation content

This tool produces a focused markdown rendering that retains the signal and discards the noise. In practice the rendered markdown is around 10-15% of the source size, with much higher density of useful text. What you do with that markdown afterward (qmd, ripgrep, grep, an embedding index, nothing at all) is up to you.

## What it does

For each session JSONL, the converter writes one markdown file containing:

- User and assistant text under clear `## User` / `## Assistant` headers, annotated with timestamps
- Tool calls rendered as fenced JSON blocks so the command and its arguments stay searchable
- Tool results collapsed to a one-line pointer back into the source JSONL, with a short structured summary when `toolUseResult` has parseable fields like `numFiles`, `mode`, or `stdout`. The full result is one `Read` away if a querying agent wants more.
- Image content blocks replaced with placeholders
- Compaction summaries kept under their own `## Compaction summary` header
- Subagent dispatches (`isSidechain: true`) marked with a `[subagent]` tag in the section header
- A YAML-style frontmatter block carrying `session_id`, `source_path`, `message_count`, `start_time`, `end_time`, and (optionally) `title` and `smart_title`

Output lands in `~/.claude/claude-md-transcripts/<basename>/`, one file per session.

## Requirements

- Python 3.12 or newer
- [uv](https://github.com/astral-sh/uv) for dependency management
- Claude Code on `PATH` as `claude` (only required for the optional `--smart-titles` and `retitle` commands)

## Installation

Install directly from GitHub with `uv tool`:

```sh
uv tool install git+https://github.com/davesque/claude-md-transcripts.git
```

This puts the `claude-md-transcripts` command on your `PATH` in an isolated environment. To upgrade later, run `uv tool upgrade claude-md-transcripts`. To uninstall, `uv tool uninstall claude-md-transcripts`.

If you'd rather work from a local clone (e.g. to hack on the tool), see the [Development](#development) section below.

Verify the install:

```sh
claude-md-transcripts --help
```

## Quick start

The fastest way in is to run `export` with no arguments. It scans `~/.claude/projects/` and shows a checklist:

```sh
claude-md-transcripts export
```

```
? Select projects to export (space to toggle, enter to confirm)
> [ ] qmd                       (5 sessions, 1.3 MB)
  [x] claude-md-transcripts     (15 sessions, 720 KB)
  [ ] nexus                     (45 sessions, 444 MB)
```

Each selected project lands under `~/.claude/claude-md-transcripts/<basename>/`. Boolean flags like `--smart-titles` and `--include-thinking` apply to every selected project.

For a single project by path:

```sh
# Convert all Claude Code sessions for one host project.
claude-md-transcripts export ~/projects/qmd

# Optional: improve filenames with LLM-generated titles.
claude-md-transcripts retitle ~/projects/qmd
```

For everything at once, no prompts:

```sh
claude-md-transcripts export-all
claude-md-transcripts retitle-all   # optional, slower, costs a small amount per session
```

## Commands

### `export`

Convert one project's sessions into markdown.

```sh
claude-md-transcripts export                       # interactive multi-select
claude-md-transcripts export HOST_PATH [OPTIONS]
claude-md-transcripts export --session-dir DIR [OPTIONS]
```

Options:

| Option | Description |
| --- | --- |
| `--output-dir DIR` | Where to write markdown for this run. Defaults to `~/.claude/claude-md-transcripts/<basename>/`. |
| `--include-thinking` | Include assistant `thinking` blocks. Off by default since extended thinking is verbose, often empty (when the engine returns encrypted signatures), and rarely useful for retrieval. |
| `--smart-titles` | Inline LLM titles via `claude -p`. Adds 5-25 seconds and a few cents per session. |
| `--max-bytes N` | Skip session files larger than N bytes (default 50 MB). Pathologically large files are usually dominated by Playwright screenshots that the renderer would discard anyway. |

`export` is idempotent: it skips any session whose source JSONL hasn't been modified since the last run. Safe to put on a cron schedule.

### `export-all`

Iterate every project under `~/.claude/projects/` and run `export` on each. The `--output-dir` flag here is the *root* under which one subdirectory per project is created (default `~/.claude/claude-md-transcripts/`).

### `retitle`

Replace heuristic filenames with LLM-generated titles for an already-exported output directory.

```sh
claude-md-transcripts retitle [HOST_PATH] [OPTIONS]
claude-md-transcripts retitle --output-dir DIR [OPTIONS]
```

Walks the output directory, calls `claude -p` once per file to summarize, updates the markdown frontmatter, and renames each file. Skips files that already carry `smart_title: true` in their frontmatter unless you pass `--force`.

This is a separate step from `export` because LLM title generation is slow and billable. Most users want fast deterministic exports (cron-friendly) followed by occasional `retitle` passes.

Options:

| Option | Description |
| --- | --- |
| `--output-dir DIR` | Output directory to retitle. Defaults to the directory derived from `HOST_PATH`. Required if you don't pass `HOST_PATH`. |
| `--force` | Re-generate titles even for files already marked smart. |

### `retitle-all`

Apply `retitle` to every subdirectory under `~/.claude/claude-md-transcripts/` (or under the path you pass via `--output-dir`).

### `inspect`

Print a diagnostic summary of one session JSONL file. Useful for spot-checking schema drift or debugging a single broken session.

```sh
claude-md-transcripts inspect ~/.claude/projects/-Users-me-projects-foo/<sid>.jsonl
```

Output includes byte size, record counts, parse errors, and a histogram of line types.

## How conversion works

### Kept

- `user` lines with prose content
- `assistant` lines with text content
- `assistant` `tool_use` blocks (rendered as fenced JSON)
- `user` `tool_result` blocks (collapsed to a one-line pointer with structured summary)
- Compaction summaries (`isCompactSummary: true`)
- Subagent / sidechain content (marked but kept in source order)
- `customTitle` lines (surfaced via frontmatter)

### Dropped

- Auxiliary line types: `permission-mode`, `attachment`, `file-history-snapshot`, `system` (mostly turn_duration metadata), `queue-operation`, `last-prompt`, `worktree-state`
- Image content blocks (replaced with a placeholder)
- Encrypted thinking signatures
- Cache-control metadata, usage tokens, requestId, and other per-line bloat

### Pointer format

A typical tool_result block in the rendered markdown looks like:

```
> tool_result for Grep (uuid=79c55ed4, see /Users/me/.claude/projects/-Users-me-projects-qmd/<sid>.jsonl#L13)
> mode='files_with_matches', numFiles=3, filenames=3
```

A querying agent that finds a hit can use the path and line number to read the full original tool result.

## File layout

```
~/.claude/claude-md-transcripts/
├── qmd/
│   ├── 2026-05-03_inspect-and-render-claude-sessions_af6ff891.md
│   └── 2026-05-04_smart-title-generation_bcdc78de.md
└── nexus/
    └── 2026-04-12_review-the-grpc-handler_55d72cab.md
```

Filename format: `<YYYY-MM-DD>_<slug>_<uuid8>.md`. The 8-character UUID prefix keeps filenames unique even when slugs collide.

### Frontmatter

Each markdown file starts with a frontmatter block:

```
---
session_id: af6ff891-b945-426b-b678-18798e66b843
source_path: /Users/me/.claude/projects/-Users-me-projects-qmd/af6ff891-...jsonl
message_count: 322
start_time: 2026-05-03T06:30:37.359Z
end_time: 2026-05-03T23:57:08.964Z
title: Optional Claude-Code-generated custom title
smart_title: true
---
```

`title` is only present when Claude Code itself emitted a `custom-title` line for the session. `smart_title: true` is only present after `--smart-titles` or a `retitle` pass.

## Smart titles

Without smart titles, the slug comes from `customTitle` if Claude Code generated one, otherwise from a heuristic over the first and last user prose messages. This is fast and deterministic but produces filenames like `hey-claude-does-qmd-support-indexing-of-non-md-files-such-as.md`.

With `--smart-titles` (during export) or via `retitle` (after export), the tool sends the head and tail of the rendered markdown (100 lines from each end by default) to `claude -p --model claude-haiku-4-5` and asks for a 3-7 word title. The result is recorded in the file's frontmatter so subsequent runs skip already-titled files.

**Cost.** Each call uses roughly 8-15K input tokens and outputs around 10. With Haiku 4.5 pricing that's about $0.005-$0.012 per session. A full retitle pass over 100 sessions costs around $0.50-$1.50.

**Latency.** Each call typically takes 8-15 seconds. A `retitle-all` over 100 sessions takes 15-25 minutes wall time.

**Failure handling.** Any failure (missing `claude` binary, timeout, non-zero exit, empty output) silently falls back to the heuristic so a partial outage never blocks the pipeline.

## What to do with the output

The tool deliberately stops at "markdown on disk." From there you can:

- Index with [qmd](https://github.com/davesque/qmd):
  ```sh
  qmd collection add ~/.claude/claude-md-transcripts/qmd --name qmd-claude-sessions --mask "**/*.md"
  qmd update
  qmd query "the auth bug we debugged" -c qmd-claude-sessions
  ```
- Search with `ripgrep`:
  ```sh
  rg "the auth bug we debugged" ~/.claude/claude-md-transcripts/
  ```
- Pipe into any embedding-based retrieval system that takes a directory of markdown files.

Each markdown file carries `source_path` in its frontmatter, so an agent that finds a hit can replay the original session if it needs more detail than the pointer summary provides.

## Workflow recommendations

A reasonable two-step flow:

1. Run `export-all` from cron or `launchd` every hour, day, or whatever cadence fits your usage. It's deterministic, fast, and idempotent.
2. Run `retitle-all` on demand, e.g. after a productive week or when you want to re-search a backlog of sessions.

If you only have a handful of sessions, `export --smart-titles` in one shot is fine.

## Development

```sh
make install      # uv sync, including dev dependencies
make test         # pytest
make cov          # pytest --cov
make lint         # ruff check
make fmt          # ruff format
make typecheck    # ty check
make check        # lint + typecheck + tests
```

The full suite runs in well under a second. CI gates should be `make check`.

## License

MIT. See [LICENSE](LICENSE) for the full text.

## Notes and gotchas

- **Session JSONL schema is not officially documented.** Models in `claude_md_transcripts/schema.py` are derived from inspection plus community references. Forward compatibility is built in: unknown line types and unknown content blocks are skipped with a warning, not raised, so a Claude Code upgrade that introduces a new field type won't crash an export. New types just get logged for review.
- **Idempotency uses mtime.** If a session is currently active, every export run will re-render it. That's by design and cheap. The alternative (hash-based) would be slower without much practical benefit.
- **No UUID dedup.** Some session files contain lines with the same UUID but slightly different `cwd`, `promptId`, or `gitBranch`, likely from session resume/replay. The renderer keeps all of them in source order to avoid creating confusing gaps in the conversation.
- **`claude -p` creates new sessions.** Each smart-title call is itself a one-turn Claude Code session, recorded under whichever directory you invoked the command from. These tiny meta-sessions show up if you later export the project that hosted the retitle invocation. They're harmless, just brief and template-like.
````

- [ ] **Step 2: Update `CLAUDE.md`**

Apply these edits to `CLAUDE.md`:

1. Replace the opening summary line:

```markdown
# claude-md-transcripts

Python tool that converts Claude Code session JSONL transcripts into clean markdown collections on disk.
```

(Drop the "and indexes them in qmd for cross-session search" half.)

2. Replace the architecture diagram block. New diagram:

````markdown
## Architecture

The pipeline is composed of small, single-responsibility modules wired together by an orchestrator. Construction is by dependency injection, so tests substitute fakes for `claude -p` rather than monkey-patching subprocess.

```
JSONL file
  └─> reader.py         streams lines into typed records, with size + parse-error guards
      └─> schema.py     Pydantic models for line types, lenient on unknown fields
  └─> render.py         records → markdown (frontmatter + body)
      └─> slug.py       deterministic filename slug from customTitle/heuristic
      └─> smart_slug.py optional live `claude -p` for LLM-generated titles
  └─> sync.py           SyncOrchestrator: sync_session_dir, retitle_collection
      └─> paths.py      host-path ↔ encoded session-dir mapping; default output-dir helpers
      └─> frontmatter.py minimal YAML-ish parser for our markdown frontmatter
  └─> cli.py            click-based commands: export, export-all, retitle, retitle-all, inspect
```

There are no global singletons. Every external dependency (the `claude` binary, file system roots) is overridable.
````

3. Replace the "Conventions specific to this project" bullet about Pydantic etc. (no change there) and the bullet about "Subprocess wrappers (`QmdClient`, `SmartSlugGenerator`)" with:

```markdown
- Subprocess wrappers (`SmartSlugGenerator`) accept an injected runner callable. In tests, pass a `FakeRunner` that records calls; in production the default wraps `subprocess.run`.
```

4. Replace the "Things to NOT do" section with:

````markdown
## Things to NOT do

- **Don't deduplicate JSONL records by UUID.** Some sessions legitimately contain duplicated UUIDs from session resume/replay. The renderer preserves all of them in source order so the conversation tree stays intact. We discussed this explicitly when building the tool, the user wanted no dedup.
- **Don't try to render images.** Image content blocks always become a one-line placeholder regardless of size. Base64 PNGs are pure noise for retrieval.
- **Don't add the `anthropic` SDK.** Smart titles use the user's local `claude` CLI in headless mode (`claude -p`), so we avoid API key plumbing entirely.
- **Don't run `claude -p` in a tight loop.** It's a real subprocess invocation that costs money and takes ~10 seconds. Smart-title features are opt-in for that reason.
- **Don't reintroduce qmd-specific behavior into the core.** The tool's job ends at "markdown on disk." Indexers (qmd, ripgrep, embeddings) live outside this repo and are the user's choice.
````

(Drop the `qmd embed` / `qmd update` pitfall — it no longer applies.)

5. Replace the "Running smoke tests safely" section so the qmd-stub example is gone:

````markdown
## Running smoke tests safely

```sh
# Inspect a real session (no network, no LLM calls)
uv run claude-md-transcripts inspect ~/.claude/projects/-Users-me-projects-qmd/<sid>.jsonl

# Render to a temp dir without touching the default output root
uv run python -c "
from pathlib import Path
from claude_md_transcripts.reader import read_session
from claude_md_transcripts.render import render_session
md = render_session(read_session(Path.home()/'.claude/projects/-foo/<sid>.jsonl'))
print(md[:1000])
"
```

To exercise the full export pipeline end-to-end without writing into your real output root, construct a `SyncOrchestrator` with `render_config=RenderConfig()` and pass an explicit `output_dir=tmp_path/'something'` to `sync_session_dir`. The retitle smoke pattern lives in `tests/test_retitle.py` and can be lifted for ad-hoc scripts.
````

- [ ] **Step 3: Update module docstrings**

In `claude_md_transcripts/paths.py`, replace the module docstring at the top with:

```python
"""
Map host project paths to Claude Code's encoded session directories.

Also provides defaults for the markdown output root and the per-project
subdirectory naming used by the CLI.
"""
```

In `claude_md_transcripts/render.py`, replace the module docstring with:

```python
"""
Render parsed session records into markdown.

The output is designed for downstream indexing or full-text search:
text-bearing content is preserved, tool I/O is replaced with one-line
pointers back into the source JSONL, and images are dropped in favor
of a placeholder. Frontmatter carries session metadata so downstream
tools can filter without re-parsing the original file.
"""
```

In `claude_md_transcripts/sync.py`, verify the module docstring at the top is the qmd-free version added in Task 2. If it still mentions qmd, fix it now.

- [ ] **Step 4: Bump version and update description in `pyproject.toml`**

Edit `pyproject.toml`. Change:

```toml
version = "0.1.0"
description = "Convert Claude Code session JSONL transcripts to markdown and index them in qmd."
```

to:

```toml
version = "0.2.0"
description = "Convert Claude Code session JSONL transcripts to clean markdown collections."
```

- [ ] **Step 5: Run the full check**

Run: `make check`
Expected: lint, typecheck, and tests all green.

- [ ] **Step 6: Smoke-test the CLI end-to-end on a real session directory**

Pick one small project under `~/.claude/projects/`, point `export` at it with `--output-dir` set to a throwaway location, and verify the markdown is written.

```sh
uv run claude-md-transcripts export --session-dir ~/.claude/projects/<small-project-encoded-name> \
    --output-dir /tmp/cmt-smoke-test
ls /tmp/cmt-smoke-test
```

Expected: one or more `.md` files. If you see `.md` files, delete `/tmp/cmt-smoke-test` and continue.

- [ ] **Step 7: Commit**

```bash
git add README.md CLAUDE.md claude_md_transcripts/paths.py \
    claude_md_transcripts/render.py claude_md_transcripts/sync.py pyproject.toml
git commit -m "Update docs and bump version to 0.2.0 for qmd-free release"
```

---

## Self-review checklist (after the engineer finishes)

This is a checklist for the implementer to run themselves once everything is committed.

- [ ] `grep -RIn "qmd" claude_md_transcripts tests` returns zero matches except in `paths.py` docstrings or comments where the word *qmd* may legitimately appear as a project example. (No code references, no imports, no flags.)
- [ ] `grep -RIn "default_collection_name\|output_dir_for_collection\|--collection\|--description\|CLAUDE_MD_TRANSCRIPTS_QMD_BIN" claude_md_transcripts tests` returns zero matches.
- [ ] `grep -RIn "from claude_md_transcripts.qmd\|QmdClient\|QmdError" .` returns zero matches.
- [ ] `make check` is green.
- [ ] `uv run claude-md-transcripts --help` shows `export`, `export-all`, `retitle`, `retitle-all`, `inspect`. No `sync`, no `sync-all`.
- [ ] `pyproject.toml` shows `version = "0.2.0"` and a description string that does not mention qmd.
- [ ] `~/.claude/qmd-transcripts/` does not exist (already manually renamed before this work began).
