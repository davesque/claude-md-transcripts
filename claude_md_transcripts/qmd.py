"""
Thin subprocess wrapper around the ``qmd`` CLI.

The class accepts an injected runner so tests can verify call shapes without
patching ``subprocess``. Errors from ``qmd`` are surfaced as :class:`QmdError`
with stderr included so callers can decide how to react.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class _RunResult(Protocol):
    """
    Subset of ``subprocess.CompletedProcess`` that the client uses.
    """

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], _RunResult]


class QmdError(RuntimeError):
    """
    Raised when a ``qmd`` invocation returns a non-zero exit code.
    """


def _default_runner(args: list[str]) -> _RunResult:
    """
    Execute a command via ``subprocess.run`` and return its result.
    """
    return subprocess.run(args, check=False, capture_output=True, text=True)


class QmdClient:
    """
    Minimal interface to the ``qmd`` CLI for collection and context management.

    Parameters
    ----------
    runner
        Optional callable that takes an argv list and returns an object with
        ``returncode``, ``stdout``, and ``stderr``. Defaults to wrapping
        ``subprocess.run``. Inject a fake in tests.
    binary
        Name or path of the ``qmd`` executable.
    """

    def __init__(self, runner: Runner | None = None, binary: str = "qmd") -> None:
        self.binary = binary
        self._runner: Runner = runner if runner is not None else _default_runner

    def _run(self, *args: str) -> _RunResult:
        """
        Execute ``qmd`` with the given args. Raises :class:`QmdError` on failure.
        """
        argv = [self.binary, *args]
        result = self._runner(argv)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise QmdError(f"{' '.join(argv)} failed (exit {result.returncode}): {stderr}")
        return result

    def collection_exists(self, name: str) -> bool:
        """
        Return True if the collection exists.

        Implemented via ``qmd collection show <name>``, which returns a
        non-zero exit code when the collection is missing.
        """
        result = self._runner([self.binary, "collection", "show", name])
        return result.returncode == 0

    def collection_add(self, *, path: Path, name: str, mask: str) -> None:
        """
        Register a new collection rooted at ``path`` with the given mask.
        """
        self._run("collection", "add", str(path), "--name", name, "--mask", mask)

    def context_add(self, target: str, text: str) -> None:
        """
        Attach context text to a path, directory, or qmd URI.
        """
        self._run("context", "add", target, text)

    def update(self) -> None:
        """
        Re-index all collections.
        """
        self._run("update")
