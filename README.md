# claude-md-transcripts

Convert your Claude Code session JSONL transcripts into clean markdown files and index them in [qmd](https://github.com/davesque/qmd) for fast cross-session search.

## Why

Claude Code records every session as a JSONL file under `~/.claude/projects/<encoded-project-path>/<session-id>.jsonl`. Those files are dense and hard to search:

- Tool I/O is bulky (long bash output, file reads, MCP responses)
- Image attachments are stored as base64, so a single Playwright screenshot can blow a session up to hundreds of megabytes
- Per-line metadata (`sessionId`, `cwd`, `gitBranch`, `version`, etc.) repeats on almost every line
- Auxiliary line types like `file-history-snapshot`, `permission-mode`, and `attachment` carry no conversation content

Indexing the raw JSONL into a search tool wastes space and pollutes results. This tool produces a focused markdown rendering that retains the signal and discards the noise. In practice the rendered markdown is around 10-15% of the source size, with much higher density of useful text.

## What it does

For each session JSONL, the converter writes one markdown file containing:

- User and assistant text under clear `## User` / `## Assistant` headers, annotated with timestamps
- Tool calls rendered as fenced JSON blocks so the command and its arguments stay searchable
- Tool results collapsed to a one-line pointer back into the source JSONL, with a short structured summary when `toolUseResult` has parseable fields like `numFiles`, `mode`, or `stdout`. The full result is one `Read` away if a querying agent wants more.
- Image content blocks replaced with placeholders
- Compaction summaries kept under their own `## Compaction summary` header
- Subagent dispatches (`isSidechain: true`) marked with a `[subagent]` tag in the section header
- A YAML-style frontmatter block carrying `session_id`, `source_path`, `message_count`, `start_time`, `end_time`, and (optionally) `title` and `smart_title`

Output lands in `~/.claude/qmd-transcripts/<collection>/`, one file per session, registered as a qmd collection so you can query across all your sessions.

## Requirements

- Python 3.12 or newer
- [uv](https://github.com/astral-sh/uv) for dependency management
- [qmd](https://github.com/davesque/qmd) on `PATH` (used for indexing)
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

For a single project:

```sh
# Convert and index all Claude Code sessions for one host project.
claude-md-transcripts sync ~/projects/qmd \
    --description "Claude Code sessions for the qmd project"

# Optional: improve filenames with LLM-generated titles.
claude-md-transcripts retitle ~/projects/qmd

# Then query via qmd
qmd embed
qmd query "the auth bug we debugged" -c qmd-claude-sessions
```

For everything at once:

```sh
claude-md-transcripts sync-all
claude-md-transcripts retitle-all   # optional, slower, costs a small amount per session
```

## Commands

### `sync`

Convert one project's sessions into markdown and register the result with qmd.

```sh
claude-md-transcripts sync HOST_PATH [OPTIONS]
claude-md-transcripts sync --session-dir DIR [OPTIONS]
```

Options:

| Option | Description |
| --- | --- |
| `--collection NAME` | qmd collection name. Defaults to `<basename>-claude-sessions` (e.g. `qmd-claude-sessions`). |
| `--description TEXT` | Description attached as qmd context for the collection. |
| `--include-thinking` | Include assistant `thinking` blocks. Off by default since extended thinking is verbose, often empty (when the engine returns encrypted signatures), and rarely useful for retrieval. |
| `--smart-titles` | Inline LLM titles via `claude -p`. Adds 5-25 seconds and a few cents per session. |
| `--max-bytes N` | Skip session files larger than N bytes (default 50 MB). Pathologically large files are usually dominated by Playwright screenshots that the renderer would discard anyway. |

`sync` is idempotent: it skips any session whose source JSONL hasn't been modified since the last run. Safe to put on a cron schedule.

### `sync-all`

Iterate every project under `~/.claude/projects/` and run `sync` on each. Same flags as `sync` apart from per-collection settings.

### `retitle`

Replace heuristic filenames with LLM-generated titles for an already-synced collection.

```sh
claude-md-transcripts retitle [HOST_PATH] [OPTIONS]
claude-md-transcripts retitle --collection NAME [OPTIONS]
```

Walks the collection's output directory, calls `claude -p` once per file to summarize, updates the markdown frontmatter, and renames each file. Skips files that already carry `smart_title: true` in their frontmatter unless you pass `--force`.

This is a separate step from `sync` because LLM title generation is slow and billable. Most users want fast deterministic syncs (cron-friendly) followed by occasional `retitle` passes.

Options:

| Option | Description |
| --- | --- |
| `--collection NAME` | Collection to retitle. Required if you don't pass `HOST_PATH`. |
| `--force` | Re-generate titles even for files already marked smart. |

### `retitle-all`

Apply `retitle` to every collection under `~/.claude/qmd-transcripts/`.

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
~/.claude/qmd-transcripts/
├── qmd-claude-sessions/
│   ├── 2026-05-03_inspect-and-render-claude-sessions_af6ff891.md
│   └── 2026-05-04_smart-title-generation_bcdc78de.md
└── nexus-claude-sessions/
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

With `--smart-titles` (during sync) or via `retitle` (after sync), the tool sends the head and tail of the rendered markdown (100 lines from each end by default) to `claude -p --model claude-haiku-4-5` and asks for a 3-7 word title. The result is recorded in the file's frontmatter so subsequent runs skip already-titled files.

**Cost.** Each call uses roughly 8-15K input tokens and outputs around 10. With Haiku 4.5 pricing that's about $0.005-$0.012 per session. A full retitle pass over 100 sessions costs around $0.50-$1.50.

**Latency.** Each call typically takes 8-15 seconds. A `retitle-all` over 100 sessions takes 15-25 minutes wall time.

**Failure handling.** Any failure (missing `claude` binary, timeout, non-zero exit, empty output) silently falls back to the heuristic so a partial outage never blocks the pipeline.

## Workflow recommendations

A reasonable two-step flow:

1. Run `sync-all` from cron or `launchd` every hour, day, or whatever cadence fits your usage. It's deterministic, fast, and idempotent.
2. Run `retitle-all` on demand, e.g. after a productive week or when you want to re-search a backlog of sessions.

If you only have a handful of sessions, `sync --smart-titles` in one shot is fine.

## Querying

Once a collection is synced, you can use any of qmd's commands against it:

```sh
qmd query "the auth bug we debugged" -c qmd-claude-sessions
qmd ls qmd-claude-sessions
qmd vsearch "bedrock claude code"
```

Each markdown's frontmatter carries `source_path`, so an agent that finds a hit can replay the original session if it needs more detail than the pointer summary provides.

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

- **Session JSONL schema is not officially documented.** Models in `claude_md_transcripts/schema.py` are derived from inspection plus community references. Forward compatibility is built in: unknown line types and unknown content blocks are skipped with a warning, not raised, so a Claude Code upgrade that introduces a new field type won't crash a sync. New types just get logged for review.
- **Idempotency uses mtime.** If a session is currently active, every sync run will re-render it. That's by design and cheap. The alternative (hash-based) would be slower without much practical benefit.
- **No UUID dedup.** Some session files contain lines with the same UUID but slightly different `cwd`, `promptId`, or `gitBranch`, likely from session resume/replay. The renderer keeps all of them in source order to avoid creating confusing gaps in the conversation.
- **`claude -p` creates new sessions.** Each smart-title call is itself a one-turn Claude Code session, recorded under whichever directory you invoked the command from. These tiny meta-sessions show up if you later sync the project that hosted the retitle invocation. They're harmless, just brief and template-like.
