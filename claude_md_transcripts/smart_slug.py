"""
LLM-driven slug generation via Claude Code's headless mode (``claude -p``).

The user is already authenticated to Claude Code, so this avoids the API-key
plumbing the Anthropic SDK would need. The cost is one subprocess invocation
per session, which is fine for a sync that runs occasionally and not in a
hot loop. Failure modes (claude missing, timeout, non-zero exit, empty
output) all return ``None`` so callers can fall back to the heuristic slug.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HEAD_LINES: int = 100
DEFAULT_TAIL_LINES: int = 100
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_MODEL: str | None = "claude-haiku-4-5"

_PROMPT = (
    "You are summarizing a Claude Code session transcript. "
    "Produce a concise 3-7 word title that captures what the session was about. "
    "Output ONLY the title text, no explanation, no quotes, no trailing punctuation.\n\n"
    "--- TRANSCRIPT ---\n"
    "{sample}\n"
    "--- END TRANSCRIPT ---\n"
)

Runner = Callable[..., Any]


def _default_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """
    Default subprocess runner used in production.
    """
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


class SmartSlugGenerator:
    """
    Generate a session title by asking Claude (headless) to summarize.

    Parameters
    ----------
    head_lines, tail_lines
        Number of lines from the start and end of the rendered markdown to
        send. The middle of the conversation is elided to keep the prompt
        small while preserving the framing of the session.
    timeout
        Subprocess timeout in seconds.
    model
        Optional model name passed via ``--model``. Defaults to a small,
        fast model suitable for short summarization.
    binary
        Path or name of the ``claude`` executable.
    runner
        Subprocess runner; injected for tests.
    """

    def __init__(
        self,
        *,
        head_lines: int = DEFAULT_HEAD_LINES,
        tail_lines: int = DEFAULT_TAIL_LINES,
        timeout: float = DEFAULT_TIMEOUT,
        model: str | None = DEFAULT_MODEL,
        binary: str = "claude",
        runner: Runner | None = None,
    ) -> None:
        self.head_lines = head_lines
        self.tail_lines = tail_lines
        self.timeout = timeout
        self.model = model
        self.binary = binary
        self._runner: Runner = runner if runner is not None else _default_runner

    def generate(self, markdown: str) -> str | None:
        """
        Build a prompt from ``markdown`` and ask Claude for a title.

        Returns
        -------
        str | None
            The cleaned title, or ``None`` on any failure (so callers can
            fall back to a deterministic heuristic).
        """
        sample = self._build_sample(markdown)
        prompt = _PROMPT.format(sample=sample)
        argv: list[str] = [self.binary, "-p"]
        if self.model:
            argv += ["--model", self.model]
        argv.append(prompt)
        try:
            result = self._runner(argv, timeout=self.timeout)
        except FileNotFoundError:
            logger.warning("claude binary %r not found; smart titles disabled", self.binary)
            return None
        except subprocess.TimeoutExpired:
            logger.warning("claude -p timed out after %.0fs; falling back", self.timeout)
            return None
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("claude -p failed unexpectedly: %s", e)
            return None
        if result.returncode != 0:
            logger.warning(
                "claude -p exited %d: %s",
                result.returncode,
                (result.stderr or "").strip()[:200],
            )
            return None
        return _clean_title(result.stdout)

    def _build_sample(self, markdown: str) -> str:
        """
        Take the first ``head_lines`` and last ``tail_lines`` of ``markdown``.

        If the markdown fits entirely in the window, the full text is used.
        Otherwise the middle is replaced with a truncation marker.
        """
        lines = markdown.splitlines()
        if len(lines) <= self.head_lines + self.tail_lines:
            return "\n".join(lines)
        head = lines[: self.head_lines]
        tail = lines[-self.tail_lines :]
        return "\n".join([*head, "", "[...middle truncated...]", "", *tail])


def _clean_title(raw: str) -> str | None:
    """
    Strip whitespace, surrounding quotes, and trailing punctuation.
    """
    s = raw.strip()
    # Drop wrapping quotes if present.
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        s = s[1:-1].strip()
    # Strip trailing sentence-final punctuation.
    s = s.rstrip(".!?,;:")
    s = s.strip()
    return s or None
