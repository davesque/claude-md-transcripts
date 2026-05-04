from __future__ import annotations

from pathlib import Path

from claude_md_transcripts.discovery import ProjectInfo
from claude_md_transcripts.picker import (
    build_choice_label,
    pick_projects,
)


def make_info(name: str, sessions: int = 3, size: int = 12345) -> ProjectInfo:
    return ProjectInfo(
        session_dir=Path("/tmp/sessions") / f"-{name}",
        host_path=Path("/Users/me/projects") / name,
        basename=name,
        session_count=sessions,
        total_size=size,
    )


def test_build_choice_label_includes_basename_count_and_size():
    info = make_info("foo", sessions=5, size=2_500_000)
    label = build_choice_label(info)
    assert "foo" in label
    assert "5 sessions" in label
    assert "MB" in label or "KB" in label


def test_build_choice_label_handles_singular_session():
    info = make_info("solo", sessions=1, size=1024)
    assert "1 session" in build_choice_label(info)


def test_pick_projects_passes_choices_to_prompter():
    captured: dict = {}

    def fake_prompter(message: str, choices: list, hint: str) -> list[ProjectInfo]:
        captured["message"] = message
        captured["choices"] = choices
        captured["hint"] = hint
        # Return the second project as selected
        return [choices[1]["value"]]

    a = make_info("alpha")
    b = make_info("bravo")
    c = make_info("charlie")

    result = pick_projects([a, b, c], prompter=fake_prompter)
    assert result == [b]
    assert "Select" in captured["message"]
    assert len(captured["choices"]) == 3
    # Each choice has at least a title and value
    assert all("title" in ch and "value" in ch for ch in captured["choices"])


def test_pick_projects_returns_empty_list_when_user_picks_nothing():
    def fake_prompter(message, choices, hint):
        return []

    result = pick_projects([make_info("foo")], prompter=fake_prompter)
    assert result == []


def test_pick_projects_returns_none_when_user_cancels():
    def fake_prompter(message, choices, hint):
        return None  # questionary returns None on cancel/Ctrl-C

    result = pick_projects([make_info("foo")], prompter=fake_prompter)
    assert result is None


def test_pick_projects_with_empty_input_returns_empty_without_prompting():
    calls = []

    def fake_prompter(message, choices, hint):
        calls.append((message, choices))
        return []

    result = pick_projects([], prompter=fake_prompter)
    assert result == []
    assert calls == []  # Don't bother the user when there's nothing to pick
