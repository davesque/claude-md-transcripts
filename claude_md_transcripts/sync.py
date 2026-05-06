"""
Orchestrate end-to-end conversion of a session directory into a markdown collection.

The orchestrator wires together :mod:`reader`, :mod:`render`, :mod:`slug`,
and :mod:`paths`. It is constructed by injection so callers (CLI, tests,
future schedulers) can swap out the render config or smart-slug generator
without touching this module.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import has_field, replace_fields
from .paths import output_dir_for_collection, resolve_session_dir
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
    Convert a Claude Code session directory and write markdown files.

    Parameters
    ----------
    render_config
        Render toggles passed through to :func:`render_session`.
    output_root
        Root directory for generated markdown. The orchestrator writes into
        ``output_root / <collection>/`` so multiple collections can coexist.
    max_bytes
        Pass-through to the reader for skip-with-warn on huge files.
    smart_slug_generator
        Optional generator used to derive LLM-assisted titles during sync.
    """

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

    def _output_dir_for(self, collection: str) -> Path:
        """
        Resolve the output directory for a collection, honoring ``output_root``.
        """
        if self.output_root is not None:
            return self.output_root / collection
        return output_dir_for_collection(collection)

    def _convert_one(
        self,
        jsonl_path: Path,
        out_dir: Path,
        summary: SyncResult,
        *,
        index: int = 0,
        total: int = 0,
    ) -> None:
        """
        Convert a single session, applying mtime-based idempotency.
        """
        progress = f"[{index}/{total}] " if total else ""
        size_kb = jsonl_path.stat().st_size / 1024
        existing = self._existing_output_for(out_dir, jsonl_path)
        if existing is not None and existing.stat().st_mtime_ns >= jsonl_path.stat().st_mtime_ns:
            logger.info("%sunchanged: %s (%.0f KB)", progress, jsonl_path.name, size_kb)
            summary.files_unchanged += 1
            return

        logger.info("%sconverting: %s (%.0f KB)", progress, jsonl_path.name, size_kb)
        result = read_session(jsonl_path, max_bytes=self.max_bytes)
        if result.skipped_for_size:
            logger.info(
                "%sskipped (size): %s (%.0f KB exceeds %.0f MB)",
                progress,
                jsonl_path.name,
                size_kb,
                self.max_bytes / 1e6,
            )
            summary.files_skipped_for_size += 1
            return
        kept = list(result.iter_kept())
        if not kept:
            logger.info("%sskipped (empty): %s", progress, jsonl_path.name)
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
        out_kb = len(markdown) / 1024
        logger.info(
            "%swrote: %s (%.0f KB, %d records, smart_title=%s)",
            progress,
            target.name,
            out_kb,
            len(kept),
            smart_used,
        )
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

    def _retitle_one(
        self, md_path: Path, *, force: bool, index: int = 0, total: int = 0
    ) -> str:
        """
        Retitle a single markdown file. Returns a status string.
        """
        progress = f"[{index}/{total}] " if total else ""
        generator = self.smart_slug_generator
        if generator is None:
            return "failed"
        text = md_path.read_text(encoding="utf-8")
        if not force and has_field(text, "smart_title", "true"):
            logger.info("%salready smart: %s", progress, md_path.name)
            return "already_smart"
        logger.info("%scalling claude -p: %s", progress, md_path.name)
        t0 = time.perf_counter()
        smart = generator.generate(text)
        elapsed = time.perf_counter() - t0
        if not smart:
            logger.warning(
                "%sno title returned for %s (%.1fs)", progress, md_path.name, elapsed
            )
            return "failed"
        new_slug = slugify_title(smart)
        new_text = replace_fields(text, smart_title="true")
        new_path = self._renamed_path_for(md_path, new_slug)
        if new_path != md_path:
            md_path.unlink()
        new_path.write_text(new_text, encoding="utf-8")
        if new_path != md_path:
            logger.info(
                "%sretitled in %.1fs: %r -> %s", progress, elapsed, smart, new_path.name
            )
        else:
            logger.info("%sretitled in %.1fs: %r (no rename)", progress, elapsed, smart)
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
