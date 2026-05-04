from __future__ import annotations

import subprocess
from dataclasses import dataclass

from claude_md_transcripts.qmd import QmdClient, QmdError


@dataclass
class FakeResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class FakeRunner:
    """A subprocess runner that records calls and returns canned results."""

    def __init__(self, responses: list[FakeResult] | None = None):
        self.calls: list[list[str]] = []
        self.responses = responses or []

    def __call__(self, args: list[str]) -> FakeResult:
        self.calls.append(args)
        if self.responses:
            return self.responses.pop(0)
        return FakeResult(returncode=0)


def test_collection_exists_returns_true_when_show_succeeds():
    runner = FakeRunner([FakeResult(returncode=0, stdout="Collection: my-coll\n  Path: ...\n")])
    client = QmdClient(runner=runner)
    assert client.collection_exists("my-coll") is True
    assert runner.calls == [["qmd", "collection", "show", "my-coll"]]


def test_collection_exists_returns_false_when_show_fails():
    runner = FakeRunner([FakeResult(returncode=1, stdout="", stderr="Collection not found")])
    client = QmdClient(runner=runner)
    assert client.collection_exists("missing") is False


def test_collection_add_invokes_correct_args(tmp_path):
    runner = FakeRunner([FakeResult(returncode=0)])
    client = QmdClient(runner=runner)
    client.collection_add(path=tmp_path, name="cc-sessions", mask="**/*.md")
    assert runner.calls == [
        ["qmd", "collection", "add", str(tmp_path), "--name", "cc-sessions", "--mask", "**/*.md"]
    ]


def test_collection_add_raises_on_failure(tmp_path):
    runner = FakeRunner([FakeResult(returncode=2, stderr="boom")])
    client = QmdClient(runner=runner)
    try:
        client.collection_add(path=tmp_path, name="x", mask="**/*.md")
    except QmdError as e:
        assert "boom" in str(e)
    else:
        raise AssertionError("expected QmdError")


def test_context_add_uses_qmd_uri_when_provided():
    runner = FakeRunner([FakeResult(returncode=0)])
    client = QmdClient(runner=runner)
    client.context_add("qmd://my-coll/", "Description here")
    assert runner.calls == [["qmd", "context", "add", "qmd://my-coll/", "Description here"]]


def test_update_runs_qmd_update():
    runner = FakeRunner([FakeResult(returncode=0)])
    client = QmdClient(runner=runner)
    client.update()
    assert runner.calls == [["qmd", "update"]]


def test_update_raises_on_failure():
    runner = FakeRunner([FakeResult(returncode=1, stderr="indexing error")])
    client = QmdClient(runner=runner)
    try:
        client.update()
    except QmdError as e:
        assert "indexing error" in str(e)
    else:
        raise AssertionError("expected QmdError")


def test_default_runner_is_subprocess_run(monkeypatch):
    """When no runner is injected, the client uses subprocess.run."""
    seen: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = QmdClient()
    client.collection_exists("foo")
    assert seen == [["qmd", "collection", "show", "foo"]]
