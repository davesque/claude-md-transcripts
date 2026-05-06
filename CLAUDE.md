# claude-md-transcripts

Python tool that converts Claude Code session JSONL transcripts into clean markdown collections on disk.

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

## Conventions specific to this project

- Top-level package layout (`claude_md_transcripts/`), not under `src/`. Imports are `from claude_md_transcripts.X import Y`.
- `uv` for dependency management, `ruff` for lint and format, `ty` for typecheck, `pytest` + coverage for tests. Coverage gate is 80% (currently around 87%).
- `make check` is the unified gate (`lint + typecheck + tests`).
- Pydantic models all use `extra="ignore"` so unknown fields don't break parsing across Claude Code versions. New top-level types or content blocks get a warning and a `SkippedLine`, never an exception.
- Subprocess wrappers (`SmartSlugGenerator`) accept an injected runner callable. In tests, pass a `FakeRunner` that records calls; in production the default wraps `subprocess.run`.
- Multi-line numpy-style docstrings on all public functions and classes (matches the global rule from `~/.claude/CLAUDE.md`).

## Test layout

Each module has a matching `tests/test_<module>.py`. Fixtures live under `tests/fixtures/`. The schema/reader/render tests use a real-but-sanitized session fixture (`sample_session.jsonl`) with synthesized lines appended for variants the real session didn't capture (image blocks, sidechain, compact summary, customTitle).

Run focused subsets with:

```sh
uv run pytest tests/test_render.py -v
uv run pytest -k retitle
```

## Things to NOT do

- **Don't deduplicate JSONL records by UUID.** Some sessions legitimately contain duplicated UUIDs from session resume/replay. The renderer preserves all of them in source order so the conversation tree stays intact. We discussed this explicitly when building the tool, the user wanted no dedup.
- **Don't try to render images.** Image content blocks always become a one-line placeholder regardless of size. Base64 PNGs are pure noise for retrieval.
- **Don't add the `anthropic` SDK.** Smart titles use the user's local `claude` CLI in headless mode (`claude -p`), so we avoid API key plumbing entirely.
- **Don't run `claude -p` in a tight loop.** It's a real subprocess invocation that costs money and takes ~10 seconds. Smart-title features are opt-in for that reason.
- **Don't reintroduce qmd-specific behavior into the core.** The tool's job ends at "markdown on disk." Indexers (qmd, ripgrep, embeddings) live outside this repo and are the user's choice.

## JSONL schema notes

The Claude Code session JSONL format is not officially documented. Models in `schema.py` are derived from inspection plus the community write-up at `databunny.medium.com/inside-claude-code-the-session-file-format-and-how-to-inspect-it`.

Top-level `type` values currently handled:

- **Kept**: `user`, `assistant`, `custom-title`, plus user lines with `isCompactSummary: true` (routed to `CompactSummaryLine`)
- **Skipped (auxiliary)**: `permission-mode`, `attachment`, `file-history-snapshot`, `system`, `queue-operation`, `last-prompt`, `worktree-state`

Content block types inside `message.content`:

- Assistant: `text`, `thinking`, `tool_use`
- User: `text`, `tool_result`
- Inside `tool_result.content`: `text`, `image`, `tool_reference`

When Claude Code adds new types, the lenient parser warns and drops them. Add explicit handling later if the new type matters.

## Path encoding

Claude Code encodes a host project path into a directory name by replacing both `/` and `.` with `-`:

- `/Users/david.sanders/projects/qmd` → `-Users-david-sanders-projects-qmd`
- `/Users/me/projects/foo/.claude/x` → `-Users-me-projects-foo--claude-x`

The encoding is lossy in reverse (we can't tell `/` apart from `.`), but forward encoding is well-defined. See `paths.py:encode_host_path`.

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

## Dependencies

Runtime: `pydantic`, `python-slugify`, `click`. Dev: `pytest`, `pytest-cov`, `ruff`, `ty`. No optional dependencies.
