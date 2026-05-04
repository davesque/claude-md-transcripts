"""
Orchestrate end-to-end conversion of a session directory into a qmd collection.

The orchestrator wires together :mod:`reader`, :mod:`render`, :mod:`slug`,
:mod:`paths`, and the :class:`~claude_md_transcripts.qmd.QmdClient` wrapper.
It is constructed by injection so callers (CLI, tests, future schedulers)
can swap out the qmd client or render config without touching this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import has_field, replace_fields
from .paths import output_dir_for_collection, resolve_session_dir
from .qmd import QmdClient
from .reader import DEFAULT_MAX_BYTES, ReaderResult, read_session
from .render import RenderConfig, render_session
from .slug import build_filename, pick_slug, slugify_title
from .smart_slug import SmartSlugGenerator

logger = logging.getLogger(__name__)


def default_collection_name(session_dir: Path) -> str:
    """
    Derive a sensible collection name from a Claude Code session directory.

    The encoded directory ``-Users-foo-projects-qmd`` becomes
    ``qmd-claude-sessions``. The ``-claude-sessions`` suffix disambiguates
    transcript collections from other qmd collections that may share a name
    with the project basename.
    """
    name = session_dir.name.lstrip("-")
    basename = name.rsplit("-", 1)[-1] if "-" in name else name
    return f"{basename or 'unknown'}-claude-sessions"


@dataclass
class SyncResult:
    """
    Summary of a single sync run.
    """

    project_path: Path | None
    session_dir: Path
    collection: str
    output_dir: Path
    files_total: int = 0
    files_converted: int = 0
    files_unchanged: int = 0
    files_skipped_for_size: int = 0
    files_skipped_empty: int = 0
    converted_paths: list[Path] = field(default_factory=list)


@dataclass
class RetitleResult:
    """
    Summary of a single retitle pass.
    """

    collection: str
    output_dir: Path
    files_total: int = 0
    files_retitled: int = 0
    files_skipped_already_smart: int = 0
    files_skipped_failed: int = 0
    renamed_paths: list[tuple[Path, Path]] = field(default_factory=list)


class SyncOrchestrator:
    """
    Convert a Claude Code session directory and register it with qmd.

    Parameters
    ----------
    qmd
        The :class:`QmdClient` used to register collections, attach context,
        and trigger re-indexing.
    render_config
        Render toggles passed through to :func:`render_session`.
    output_root
        Root directory for generated markdown. The orchestrator writes into
        ``output_root / <collection>/`` so multiple collections can coexist.
    max_bytes
        Pass-through to the reader for skip-with-warn on huge files.
    """

    def __init__(
        self,
        *,
        qmd: QmdClient,
        render_config: RenderConfig,
        output_root: Path | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        smart_slug_generator: SmartSlugGenerator | None = None,
    ) -> None:
        self.qmd = qmd
        self.render_config = render_config
        self.output_root = output_root
        self.max_bytes = max_bytes
        self.smart_slug_generator = smart_slug_generator

    def sync_host_project(
        self,
        host_path: Path,
        *,
        collection: str | None = None,
        description: str | None = None,
    ) -> SyncResult:
        """
        Sync a host project by resolving its Claude Code session directory.
        """
        session_dir = resolve_session_dir(host_path)
        result = self.sync_session_dir(session_dir, collection=collection, description=description)
        result.project_path = host_path.resolve()
        return result

    def sync_session_dir(
        self,
        session_dir: Path,
        *,
        collection: str | None = None,
        description: str | None = None,
    ) -> SyncResult:
        """
        Convert all sessions in ``session_dir`` and register the result with qmd.
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

        for jsonl_path in sorted(session_dir.glob("*.jsonl")):
            summary.files_total += 1
            self._convert_one(jsonl_path, out_dir, summary)

        if not self.qmd.collection_exists(coll_name):
            self.qmd.collection_add(path=out_dir, name=coll_name, mask="**/*.md")
        if description:
            self.qmd.context_add(f"qmd://{coll_name}/", description)
        self.qmd.update()
        return summary

    def _output_dir_for(self, collection: str) -> Path:
        """
        Resolve the output directory for a collection, honoring ``output_root``.
        """
        if self.output_root is not None:
            return self.output_root / collection
        return output_dir_for_collection(collection)

    def _convert_one(self, jsonl_path: Path, out_dir: Path, summary: SyncResult) -> None:
        """
        Convert a single session, applying mtime-based idempotency.
        """
        existing = self._existing_output_for(out_dir, jsonl_path)
        if existing is not None and existing.stat().st_mtime_ns >= jsonl_path.stat().st_mtime_ns:
            summary.files_unchanged += 1
            return

        result = read_session(jsonl_path, max_bytes=self.max_bytes)
        if result.skipped_for_size:
            summary.files_skipped_for_size += 1
            return
        kept = list(result.iter_kept())
        if not kept:
            summary.files_skipped_empty += 1
            return

        markdown = render_session(result, self.render_config)
        slug, smart_used = self._pick_slug_with_source(result, markdown)
        if smart_used:
            markdown = replace_fields(markdown, smart_title="true")
        target = out_dir / self._build_filename(result, jsonl_path, slug)
        # Remove any stale file with a different slug for this session.
        if existing is not None and existing != target:
            existing.unlink(missing_ok=True)
        target.write_text(markdown, encoding="utf-8")
        summary.files_converted += 1
        summary.converted_paths.append(target)

    def _existing_output_for(self, out_dir: Path, jsonl_path: Path) -> Path | None:
        """
        Look for a previously-generated markdown file for the same session.

        Matching is by the session UUID's first 8 chars, which is part of the
        canonical filename, so renames driven by an updated slug still match.
        """
        uuid8 = jsonl_path.stem.split("-", 1)[0][:8]
        if not uuid8:
            return None
        matches = list(out_dir.glob(f"*_{uuid8}.md"))
        return matches[0] if matches else None

    def _pick_slug_with_source(
        self, result: ReaderResult, markdown: str
    ) -> tuple[str, bool]:
        """
        Pick a slug and report whether the smart generator was used.

        Returns
        -------
        slug
            The slug to use in the filename.
        smart_used
            True if the slug came from :class:`SmartSlugGenerator` and
            therefore the frontmatter should be marked ``smart_title: true``.
        """
        if result.custom_title:
            return slugify_title(result.custom_title), False
        if self.smart_slug_generator is not None:
            smart = self.smart_slug_generator.generate(markdown)
            if smart:
                return slugify_title(smart), True
        return pick_slug(result), False

    def _build_filename(self, result: ReaderResult, jsonl_path: Path, slug: str) -> str:
        """
        Build the markdown filename for a parsed session given a chosen slug.
        """
        timestamp = "0000-00-00T00:00:00Z"
        for rec in result.records:
            ts = getattr(rec.parsed, "timestamp", None)
            if isinstance(ts, str) and ts:
                timestamp = ts
                break
        # Use the file's own UUID (its stem) so output filenames remain stable
        # even if the first record's UUID isn't the session UUID.
        return build_filename(timestamp=timestamp, slug=slug, uuid=jsonl_path.stem)

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
        on the existing markdown body, updates the frontmatter, and
        renames the file when the resulting slug differs.

        Triggers ``qmd update`` after touching files so the index reflects
        any renames.
        """
        if self.smart_slug_generator is None:
            raise ValueError("retitle_collection requires a smart_slug_generator")
        out_dir = self._output_dir_for(collection)
        result = RetitleResult(collection=collection, output_dir=out_dir)
        if not out_dir.exists():
            return result
        any_changes = False
        for md_path in sorted(out_dir.glob("*.md")):
            result.files_total += 1
            outcome = self._retitle_one(md_path, force=force)
            if outcome == "retitled":
                result.files_retitled += 1
                any_changes = True
            elif outcome == "already_smart":
                result.files_skipped_already_smart += 1
            elif outcome == "failed":
                result.files_skipped_failed += 1
        if any_changes:
            self.qmd.update()
        return result

    def _retitle_one(self, md_path: Path, *, force: bool) -> str:
        """
        Retitle a single markdown file. Returns a status string.
        """
        generator = self.smart_slug_generator
        if generator is None:
            return "failed"
        text = md_path.read_text(encoding="utf-8")
        if not force and has_field(text, "smart_title", "true"):
            return "already_smart"
        smart = generator.generate(text)
        if not smart:
            logger.warning("smart-title generation returned nothing for %s", md_path.name)
            return "failed"
        new_slug = slugify_title(smart)
        new_text = replace_fields(text, smart_title="true")
        new_path = self._renamed_path_for(md_path, new_slug)
        if new_path != md_path:
            md_path.unlink()
        new_path.write_text(new_text, encoding="utf-8")
        return "retitled"

    def _renamed_path_for(self, md_path: Path, new_slug: str) -> Path:
        """
        Compute the rewritten filename when a slug changes.

        Filename layout is ``<date>_<slug>_<uuid8>.md``; we keep the date
        and uuid8 segments and replace only the slug.
        """
        stem = md_path.stem
        try:
            date_part, _, rest = stem.partition("_")
            uuid_part = rest.rsplit("_", 1)[-1]
        except ValueError:
            return md_path
        new_name = build_filename(
            timestamp=date_part + "T00:00:00Z",
            slug=new_slug,
            uuid=uuid_part,
        )
        return md_path.with_name(new_name)
