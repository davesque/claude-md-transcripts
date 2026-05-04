"""
Generate human-readable filenames for converted session transcripts.

The slug fallback chain handled here:
1. ``customTitle`` from a ``custom-title`` line, if Claude Code generated one.
2. Heuristic from the first and last user prose messages.

Smart-title generation lives in :mod:`smart_slug` and is composed by callers
(typically :class:`SyncOrchestrator`), not by this module, so the basic
fallback stays pure and side-effect-free.
"""

from __future__ import annotations

from slugify import slugify

from .reader import ReaderResult
from .schema import TextBlock, UserLine

MAX_SLUG_LEN: int = 60
FILENAME_MAX_LEN: int = 120


def pick_slug(result: ReaderResult) -> str:
    """
    Pick the best deterministic slug for a parsed session.

    Tries ``customTitle`` first, then a heuristic from user messages,
    then ``"untitled"`` as a last resort. No I/O.
    """
    if result.custom_title:
        return slugify(result.custom_title, max_length=MAX_SLUG_LEN, word_boundary=True)
    heuristic = fallback_slug_from_messages(result)
    return heuristic or "untitled"


def slugify_title(text: str) -> str:
    """
    Slugify a free-form title string with this module's defaults.
    """
    return slugify(text, max_length=MAX_SLUG_LEN, word_boundary=True)


def fallback_slug_from_messages(result: ReaderResult) -> str:
    """
    Derive a slug from the first and last non-empty user prose messages.

    Tool-result-only user lines are ignored, so the slug always reflects
    actual user prompts rather than tool plumbing.
    """
    user_texts: list[str] = []
    for rec in result.records:
        line = rec.parsed
        if not isinstance(line, UserLine):
            continue
        if line.is_compact_summary:
            continue
        for block in line.content_blocks:
            if isinstance(block, TextBlock) and block.text.strip():
                user_texts.append(block.text.strip())
                break
    if not user_texts:
        return ""
    first = user_texts[0]
    last = user_texts[-1]
    combined = first if first == last else f"{first} {last}"
    return slugify(combined, max_length=MAX_SLUG_LEN, word_boundary=True)


def build_filename(*, timestamp: str, slug: str, uuid: str) -> str:
    """
    Build the final markdown filename for a converted session.

    Format: ``<YYYY-MM-DD>_<slug>_<uuid8>.md`` where ``uuid8`` is the first
    eight characters of the session UUID.
    """
    date = timestamp[:10] if timestamp else "0000-00-00"
    uuid8 = (uuid or "").split("-")[0][:8] or "00000000"
    fname = f"{date}_{slug}_{uuid8}.md"
    if len(fname) >= FILENAME_MAX_LEN:
        # Trim slug to fit; keep date prefix and uuid suffix.
        fixed_overhead = len(date) + len(uuid8) + len("__.md") + 1
        max_slug = FILENAME_MAX_LEN - fixed_overhead
        fname = f"{date}_{slug[:max_slug]}_{uuid8}.md"
    return fname
