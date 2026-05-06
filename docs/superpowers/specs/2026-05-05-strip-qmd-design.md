---
title: Strip qmd integration; reframe as a general Claude Code → markdown exporter
date: 2026-05-05
status: approved
---

# Strip qmd integration

## Goal

Reframe `claude-md-transcripts` from a qmd-fronted indexing pipeline into a general-purpose exporter: take Claude Code session JSONL files and produce clean markdown directories on disk. Whatever the user does with the markdown afterward (qmd, ripgrep, another indexer, or nothing) is out of scope.

## Why

The tool's core value is the JSONL → markdown rendering: dropping noise (image base64, cache metadata, auxiliary line types), preserving signal (user/assistant text, tool calls, tool-result pointers), and producing one focused file per session. qmd integration is a layer on top of that core, useful but optional. Embedding it as a hard dependency conflates two responsibilities and forces every user to adopt qmd whether they want to or not. Stripping it makes the tool simpler, easier to recommend, and trivially composable with any downstream indexer.

## Non-goals

- No plugin / hook architecture for indexers. If a second indexer is ever needed, that is a future spec.
- No migration command in the tool itself. The user has already manually renamed their old `~/.claude/qmd-transcripts/` directory (and its `<basename>-claude-sessions/` subdirs) to match the new layout.
- No backwards compatibility for the old CLI flags or env var. Repo is at 0.1.0 with no external users; clean breaks are fine.

## Architecture

The pipeline keeps its current shape but loses its tail:

```
JSONL → reader → render → SyncOrchestrator → markdown files on disk
```

Previously the orchestrator additionally called `qmd.collection_exists` / `collection_add` / `context_add` / `update` after writing files. Those calls go away.

### Module changes

- **Delete** `claude_md_transcripts/qmd.py` (the `QmdClient` wrapper).
- **`sync.py`**:
  - `SyncOrchestrator` class name is unchanged (still describes mtime-idempotent sync behavior accurately).
  - `SyncOrchestrator.__init__` drops the `qmd: QmdClient` parameter.
  - `sync_session_dir` no longer calls any qmd methods. Its job is now: convert JSONL files in `session_dir` into markdown under `output_dir`, idempotently.
  - `retitle_collection` likewise drops the trailing `qmd.update()`.
  - Parameter rename: `collection` / `coll_name` → `output_dir` (callers now pass the path directly rather than a name to be resolved).
  - `RetitleResult.collection` field is dropped; the existing `output_dir` field already carries the same information.
  - `default_collection_name(session_dir)` is removed from `sync.py` (relocates to `paths.py` as `default_subdir_name`; see below).
- **`paths.py`** — replace `output_dir_for_collection(collection)` with three helpers:
  - `default_output_root() -> Path` returning `~/.claude/claude-md-transcripts/`
  - `default_subdir_name(session_dir) -> str` returning bare `<basename>` (no `-claude-sessions` suffix). Moved here from `sync.py` so the path helpers stay grouped.
  - `default_output_dir_for(session_dir) -> Path` returning `default_output_root() / default_subdir_name(session_dir)`.
- **`cli.py`**:
  - Drop `_make_orchestrator`'s qmd construction.
  - Drop the `CLAUDE_MD_TRANSCRIPTS_QMD_BIN` env var.
  - Rename commands and reshape flags (see CLI Surface).

### Pre-existing limitation (not addressed here)

Two host projects with the same basename collide under the default subdir layout (e.g., `~/projects/foo` and `~/work/foo` both default to `~/.claude/claude-md-transcripts/foo/`). This is the same collision risk as today, just with a different parent directory. Out of scope for this refactor; the user can pass `--output-dir` to disambiguate.

## CLI Surface

Final command list:

```
claude-md-transcripts export [HOST_PATH] [OPTIONS]
claude-md-transcripts export-all [OPTIONS]
claude-md-transcripts retitle [HOST_PATH] [OPTIONS]
claude-md-transcripts retitle-all [OPTIONS]
claude-md-transcripts inspect SOURCE              # unchanged
```

### `export`

| Flag | Meaning | Default |
| --- | --- | --- |
| `HOST_PATH` (positional) | Host project to export | — |
| `--session-dir DIR` | Operate on a Claude Code session dir directly | — |
| `--output-dir DIR` | Where to write markdown for this run | `~/.claude/claude-md-transcripts/<basename>/` (derived from HOST_PATH or session-dir) |
| `--include-thinking` | Include assistant `thinking` blocks | off |
| `--smart-titles` | Inline LLM titles via `claude -p` | off |
| `--max-bytes N` | Skip JSONL files larger than this | 50 MB |

Interactive multi-select (no `HOST_PATH` / `--session-dir` given) still works the same way; each selected project resolves to its own default subdir under the output root. Passing `--output-dir` in interactive mode is a usage error (it would only make sense for a single project) — same shape as today's "uniform-collection" warning, but stricter.

### `export-all`

Same flags as `export`, but `--output-dir` here is the *root* (default `~/.claude/claude-md-transcripts/`); the per-project subdirs are still derived from each project's basename.

### `retitle`

| Flag | Meaning | Default |
| --- | --- | --- |
| `HOST_PATH` (positional) | Host project whose output to retitle | — |
| `--output-dir DIR` | Output directory to retitle | `default_output_dir_for(HOST_PATH)` |
| `--force` | Re-title files already marked `smart_title: true` | off |

If neither `HOST_PATH` nor `--output-dir` is given → usage error. If `--output-dir` is given and the directory does not exist, `retitle` logs and returns an empty result (current behavior, preserved).

### `retitle-all`

| Flag | Meaning | Default |
| --- | --- | --- |
| `--output-dir DIR` | Root to walk for subdirs to retitle | `~/.claude/claude-md-transcripts/` |
| `--force` | as above | off |

### Removed

- `--collection` flag (subsumed by `--output-dir`)
- `--description` flag (no consumer post-qmd)
- `CLAUDE_MD_TRANSCRIPTS_QMD_BIN` env var
- The "uniform `--collection` / `--description` warning" branch in interactive mode

## Output layout

```
~/.claude/claude-md-transcripts/
├── qmd/
│   ├── 2026-05-03_inspect-and-render-claude-sessions_af6ff891.md
│   └── 2026-05-04_smart-title-generation_bcdc78de.md
├── nexus/
│   └── 2026-04-12_review-the-grpc-handler_55d72cab.md
└── claude-md-transcripts/
    └── 2026-05-05_strip-qmd-integration_abcd1234.md
```

Filename format unchanged: `<YYYY-MM-DD>_<slug>_<uuid8>.md`.

### Frontmatter (unchanged)

```yaml
---
session_id: af6ff891-b945-426b-b678-18798e66b843
source_path: /Users/me/.claude/projects/-Users-me-projects-qmd/af6ff891-...jsonl
message_count: 322
start_time: 2026-05-03T06:30:37.359Z
end_time: 2026-05-03T23:57:08.964Z
title: Optional Claude Code custom title
smart_title: true
---
```

No qmd-specific fields ever existed in frontmatter, so nothing to strip.

## Tests

- **Delete** `tests/test_qmd.py` (~100 lines).
- **`tests/test_sync.py`**:
  - Drop the qmd `FakeRunner` and all `qmd.collection_*` / `qmd.update` call assertions.
  - Drop the `qmd=` argument from every `SyncOrchestrator(...)` construction.
  - Move `default_collection_name` test cases to `tests/test_paths.py` and update for the renamed `default_subdir_name` (bare basename return value).
  - Tests get noticeably simpler since the orchestrator's surface narrows to "given a session dir and an output dir, produce markdown files."
- **`tests/test_retitle.py`** — drop `qmd.update` invocation assertions; keep file-rename and frontmatter-update assertions.
- **`tests/test_cli.py`**:
  - Remove the qmd-binary stub script setup.
  - Drop `CLAUDE_MD_TRANSCRIPTS_QMD_BIN` env-var manipulation.
  - Rename `sync` → `export` and `sync-all` → `export-all` throughout.
  - Drop tests for `--collection` and `--description`.
  - Add tests for `--output-dir` (single-dir form for `export` / `retitle`, root form for `export-all` / `retitle-all`).
- **`tests/test_paths.py`** — replace `output_dir_for_collection` with the two new helpers.
- Coverage gate stays at 80%. Expecting actual coverage to nudge slightly upward as deleted code (qmd wrapper) had its own tests, removed proportionally.

## Docs

- **`README.md`** — substantial rewrite:
  - New tagline focused on "convert Claude Code sessions to markdown."
  - Drop qmd from Why / What / Requirements / Quick start.
  - Replace the "Querying" section with a short "What to do with the output" pointer that mentions qmd, ripgrep, and other indexers as examples, framed explicitly as the user's choice.
  - Update file-layout example to the new path.
  - Update commands table to `export` / `export-all`.
- **`CLAUDE.md`**:
  - Update the architecture diagram (drop the qmd box).
  - Drop the "Don't invoke `qmd embed` or `qmd update`" pitfall (no longer applicable).
  - Update "Running smoke tests safely" examples (drop the `QmdClient` stubbing notes).
- **Module docstrings** — `sync.py`, `paths.py`, `render.py` all have qmd phrasing; rewrite to match the new framing.
- **`pyproject.toml`** — verify project description string isn't qmd-flavored; bump version 0.1.0 → 0.2.0 (breaking CLI change).

## Migration of existing output

Already done manually before this spec was written: the user renamed `~/.claude/qmd-transcripts/` → `~/.claude/claude-md-transcripts/` and stripped the `-claude-sessions` suffix from each subdir. No tool-side migration code is needed.
