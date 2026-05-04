"""
Interactive multi-select picker for Claude Code projects.

Wraps :mod:`questionary` so the CLI's ``sync`` command can drop into a TUI
when no project path is given. The default prompter is replaceable via
dependency injection so tests can drive the selection deterministically.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from .discovery import ProjectInfo

Choice = dict[str, Any]
Prompter = Callable[[str, list[Choice], str], list[ProjectInfo] | None]


def build_choice_label(info: ProjectInfo) -> str:
    """
    Build the one-line label shown for a project in the picker.
    """
    sessions = "1 session" if info.session_count == 1 else f"{info.session_count} sessions"
    return f"{info.basename}  ({sessions}, {info.format_size()})"


def _questionary_prompter(
    message: str, choices: list[Choice], hint: str
) -> list[ProjectInfo] | None:
    """
    Default prompter: open a questionary checkbox prompt.

    Returns the selected ``ProjectInfo`` values, or ``None`` if the user
    cancels (Ctrl-C or Ctrl-D in questionary terms).
    """
    import questionary

    q_choices = [
        questionary.Choice(
            title=ch["title"],
            value=ch["value"],
            description=ch.get("description"),
        )
        for ch in choices
    ]
    return questionary.checkbox(message, choices=q_choices, instruction=hint).ask()


def pick_projects(
    projects: list[ProjectInfo],
    *,
    prompter: Prompter | None = None,
) -> list[ProjectInfo] | None:
    """
    Show an interactive multi-select for ``projects``.

    Parameters
    ----------
    projects
        Candidates to display.
    prompter
        Optional callable that takes ``(message, choices, hint)`` and
        returns the selected ``ProjectInfo`` list, ``None`` on cancel,
        or an empty list when the user confirms with nothing checked.
        The default uses questionary; tests inject a fake.

    Returns
    -------
    list[ProjectInfo] | None
        The selection, or ``None`` if the user cancelled.
    """
    if not projects:
        return []
    chooser = prompter or _questionary_prompter
    choices: list[Choice] = []
    for info in projects:
        choices.append(
            {
                "title": build_choice_label(info),
                "value": info,
                "description": str(info.host_path) if info.host_path else str(info.session_dir),
            }
        )
    message = "Select projects to sync"
    hint = "(space to toggle, enter to confirm)"
    return chooser(message, choices, hint)


def is_tty() -> bool:
    """
    Return True when both stdin and stdout are connected to a terminal.

    Used by the CLI to decide whether falling back to TUI mode is safe.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()
