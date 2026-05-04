"""
Command-line entry point for ``claude-md-transcripts``.

The CLI is intentionally thin: each subcommand wires up the existing
:class:`SyncOrchestrator` (or the reader directly for ``inspect``) and
prints a short summary. The qmd binary path can be overridden with the
``CLAUDE_MD_TRANSCRIPTS_QMD_BIN`` environment variable so that tests and
sandboxes can substitute a stub.
"""

from __future__ import annotations

import collections
import logging
import os
from pathlib import Path

import click

from .discovery import ProjectInfo, discover_projects
from .paths import claude_projects_dir, output_dir_for_collection, resolve_session_dir
from .picker import is_tty, pick_projects
from .qmd import QmdClient
from .reader import DEFAULT_MAX_BYTES, read_session
from .render import RenderConfig
from .smart_slug import SmartSlugGenerator
from .sync import SyncOrchestrator, SyncResult, default_collection_name


def _make_orchestrator(
    *, include_thinking: bool, max_bytes: int, smart_titles: bool = False
) -> SyncOrchestrator:
    """
    Construct a default orchestrator wired to the real qmd binary.
    """
    binary = os.environ.get("CLAUDE_MD_TRANSCRIPTS_QMD_BIN", "qmd")
    smart_gen = SmartSlugGenerator() if smart_titles else None
    return SyncOrchestrator(
        qmd=QmdClient(binary=binary),
        render_config=RenderConfig(include_thinking=include_thinking),
        max_bytes=max_bytes,
        smart_slug_generator=smart_gen,
    )


def _resolve_collection_name(host_path: Path | None, collection: str | None) -> str:
    """
    Pick the collection name from either ``--collection`` or a host path.
    """
    if collection:
        return collection
    if host_path is None:
        raise click.UsageError("Provide either HOST_PATH or --collection.")
    session_dir = resolve_session_dir(host_path)
    return default_collection_name(session_dir)


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
    Convert Claude Code session JSONL transcripts to markdown for qmd.
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
@click.option("--collection", help="qmd collection name (defaults to <project>-claude-sessions).")
@click.option("--description", help="Description attached as qmd context for the collection.")
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
def sync(
    host_path: Path | None,
    session_dir: Path | None,
    collection: str | None,
    description: str | None,
    include_thinking: bool,
    smart_titles: bool,
    max_bytes: int,
) -> None:
    """
    Convert one or more host projects' sessions and register them with qmd.

    With no positional path or ``--session-dir``, drops into an interactive
    multi-select for projects discovered under ``~/.claude/projects/``.
    """
    orch = _make_orchestrator(
        include_thinking=include_thinking,
        max_bytes=max_bytes,
        smart_titles=smart_titles,
    )

    if host_path is None and session_dir is None:
        if not is_tty():
            raise click.UsageError(
                "Provide HOST_PATH or --session-dir, or run interactively in a terminal."
            )
        _run_interactive_sync(orch, collection=collection, description=description)
        return

    if collection is None and host_path is None and session_dir is not None:
        # Permit defaulting from the session dir.
        pass

    if session_dir is not None:
        result = orch.sync_session_dir(session_dir, collection=collection, description=description)
    else:
        assert host_path is not None
        try:
            result = orch.sync_host_project(
                host_path, collection=collection, description=description
            )
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e

    _print_sync_summary(result)


def _run_interactive_sync(
    orch: SyncOrchestrator,
    *,
    collection: str | None,
    description: str | None,
) -> None:
    """
    Discover projects, prompt the user to pick a subset, and sync each one.

    Boolean flags from the CLI (``--include-thinking``, ``--smart-titles``,
    ``--max-bytes``) are already baked into ``orch``. Per-project collection
    names and descriptions are auto-derived from each project's basename
    unless the user explicitly passed ``--collection`` or ``--description``
    (in which case they're applied uniformly to every selection, which is
    almost certainly not what they want and gets a warning).
    """
    projects = discover_projects(claude_projects_dir())
    if not projects:
        click.echo(f"No project directories found under {claude_projects_dir()}.")
        return

    if collection is not None or description is not None:
        click.echo(
            "Note: --collection / --description override the per-project defaults "
            "and will apply to every selected project. Skip those flags to get the "
            "default '<basename>-claude-sessions' collection and "
            "'Claude Code session transcripts for <basename>' description.",
            err=True,
        )

    selected = pick_projects(projects)
    if selected is None:
        click.echo("Cancelled.")
        return
    if not selected:
        click.echo("Nothing selected.")
        return

    results: list[SyncResult] = []
    for info in selected:
        coll = collection or _default_collection_for(info)
        desc = description or _default_description_for(info)
        click.echo(f"\n=== {info.basename} ({coll}) ===")
        result = orch.sync_session_dir(
            info.session_dir, collection=coll, description=desc
        )
        results.append(result)
        _print_sync_summary(result, indent="  ")

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


def _default_collection_for(info: ProjectInfo) -> str:
    """
    Default collection name for an interactively selected project.
    """
    return f"{info.basename}-claude-sessions"


def _default_description_for(info: ProjectInfo) -> str:
    """
    Default qmd context description for an interactively selected project.
    """
    return f"Claude Code session transcripts for {info.basename}"


@cli.command("sync-all")
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
def sync_all(include_thinking: bool, smart_titles: bool, max_bytes: int) -> None:
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
    total = collections.Counter()
    for d in project_dirs:
        coll = default_collection_name(d)
        click.echo(f"\n→ {d.name}  ({coll})")
        result = orch.sync_session_dir(d, collection=coll)
        _print_sync_summary(result, indent="  ")
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


@cli.command()
@click.argument("host_path", required=False, type=click.Path(file_okay=False, path_type=Path))
@click.option("--collection", help="Collection name (defaults to <project>-claude-sessions).")
@click.option(
    "--force",
    is_flag=True,
    help="Retitle even files that already carry smart_title: true in their frontmatter.",
)
def retitle(host_path: Path | None, collection: str | None, force: bool) -> None:
    """
    Apply smart titles to a previously-synced collection.

    Walks the collection's output directory and asks ``claude -p`` for a
    summary title for every transcript that doesn't already have one.
    Pass ``--force`` to refresh titles that were generated previously.
    """
    try:
        coll_name = _resolve_collection_name(host_path, collection)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    orch = _make_orchestrator(
        include_thinking=False,
        max_bytes=DEFAULT_MAX_BYTES,
        smart_titles=True,
    )
    result = orch.retitle_collection(coll_name, force=force)
    _print_retitle_summary(result)


@cli.command("retitle-all")
@click.option(
    "--force",
    is_flag=True,
    help="Retitle even files that already carry smart_title: true in their frontmatter.",
)
def retitle_all(force: bool) -> None:
    """
    Apply smart titles to every collection under ~/.claude/qmd-transcripts/.
    """
    orch = _make_orchestrator(
        include_thinking=False,
        max_bytes=DEFAULT_MAX_BYTES,
        smart_titles=True,
    )
    root = output_dir_for_collection("").parent
    if not root.exists():
        click.echo(f"No transcripts root at {root}.")
        return
    coll_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not coll_dirs:
        click.echo("No collections found.")
        return
    totals: collections.Counter[str] = collections.Counter()
    for d in coll_dirs:
        coll = d.name
        click.echo(f"\n→ {coll}")
        result = orch.retitle_collection(coll, force=force)
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


def _print_sync_summary(result, indent: str = "") -> None:
    """
    Render a SyncResult to stdout in a one-glance format.
    """
    click.echo(
        f"{indent}collection={result.collection} "
        f"out={result.output_dir} "
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
        f"{indent}collection={result.collection} "
        f"out={result.output_dir} "
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
