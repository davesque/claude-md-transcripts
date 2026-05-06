"""
Command-line entry point for ``claude-md-transcripts``.

Each subcommand wires up the existing :class:`Exporter` (or the
reader directly for ``inspect``) and prints a short summary.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path

import click

from .discovery import discover_projects
from .exporter import Exporter, ExportResult, RetitleResult
from .paths import (
    claude_projects_dir,
    default_output_dir_for,
    default_output_root,
    default_subdir_name,
    resolve_session_dir,
)
from .picker import is_tty, pick_projects
from .reader import DEFAULT_MAX_BYTES, read_session
from .render import RenderConfig
from .smart_slug import SmartSlugGenerator


def _make_exporter(
    *, include_thinking: bool, max_bytes: int, smart_titles: bool = False
) -> Exporter:
    """
    Construct a default exporter wired for direct markdown export.
    """
    smart_gen = SmartSlugGenerator() if smart_titles else None
    return Exporter(
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
    Convert Claude Code session JSONL transcripts to clean markdown.
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
    orch = _make_exporter(
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
        result = orch.export_session_dir(session_dir, output_dir=output_dir)
    else:
        assert host_path is not None
        try:
            result = orch.export_host_project(host_path, output_dir=output_dir)
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e

    _print_export_summary(result)


def _run_interactive_export(orch: Exporter) -> None:
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

    results: list[ExportResult] = []
    for info in selected:
        out_dir = default_output_dir_for(info.session_dir)
        click.echo(f"\n=== {info.basename} ({out_dir}) ===")
        result = orch.export_session_dir(info.session_dir, output_dir=out_dir)
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
    orch = _make_exporter(
        include_thinking=include_thinking,
        max_bytes=max_bytes,
        smart_titles=smart_titles,
    )
    root = claude_projects_dir()
    if not root.exists():
        raise click.ClickException(f"No Claude Code projects directory at {root}")
    project_dirs = sorted(
        p for p in root.iterdir() if p.is_dir() and any(p.glob("*.jsonl"))
    )
    if not project_dirs:
        click.echo("No project directories found.")
        return
    output_root = output_dir if output_dir is not None else default_output_root()
    total: collections.Counter[str] = collections.Counter()
    for d in project_dirs:
        out_dir = output_root / default_subdir_name(d)
        click.echo(f"\n→ {d.name}  ({out_dir})")
        result = orch.export_session_dir(d, output_dir=out_dir)
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
    orch = _make_exporter(
        include_thinking=False,
        max_bytes=DEFAULT_MAX_BYTES,
        smart_titles=True,
    )
    result = orch.retitle(output_dir, force=force)
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
    orch = _make_exporter(
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
        result = orch.retitle(d, force=force)
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


def _print_export_summary(result: ExportResult, indent: str = "") -> None:
    """
    Render an ExportResult to stdout in a one-glance format.
    """
    click.echo(
        f"{indent}out={result.output_dir} "
        f"total={result.files_total} "
        f"converted={result.files_converted} "
        f"unchanged={result.files_unchanged} "
        f"skipped_for_size={result.files_skipped_for_size} "
        f"skipped_empty={result.files_skipped_empty}"
    )


def _print_retitle_summary(result: RetitleResult, indent: str = "") -> None:
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
