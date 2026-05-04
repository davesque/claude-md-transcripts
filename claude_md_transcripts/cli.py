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

from .paths import claude_projects_dir, output_dir_for_collection, resolve_session_dir
from .qmd import QmdClient
from .reader import DEFAULT_MAX_BYTES, read_session
from .render import RenderConfig
from .smart_slug import SmartSlugGenerator
from .sync import SyncOrchestrator, default_collection_name


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
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
def cli(verbose: bool) -> None:
    """
    Convert Claude Code session JSONL transcripts to markdown for qmd.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
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
    Convert one host project's sessions and register them with qmd.
    """
    if host_path is None and session_dir is None:
        raise click.UsageError("Provide either HOST_PATH or --session-dir.")

    orch = _make_orchestrator(
        include_thinking=include_thinking,
        max_bytes=max_bytes,
        smart_titles=smart_titles,
    )

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
