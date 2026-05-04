from __future__ import annotations

import subprocess
from dataclasses import dataclass

from claude_md_transcripts.smart_slug import SmartSlugGenerator


@dataclass
class FakeRun:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class FakeRunner:
    """A subprocess runner stand-in that records calls."""

    def __init__(self, response: FakeRun | Exception | None = None):
        self.response = response or FakeRun()
        self.calls: list[dict] = []

    def __call__(self, args: list[str], **kwargs):
        self.calls.append({"args": args, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return subprocess.CompletedProcess(
            args=args,
            returncode=self.response.returncode,
            stdout=self.response.stdout,
            stderr=self.response.stderr,
        )


def _markdown_with_n_lines(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, n + 1))


def test_generator_invokes_claude_p_with_a_prompt():
    runner = FakeRunner(FakeRun(stdout="auth bug investigation\n"))
    gen = SmartSlugGenerator(runner=runner)
    out = gen.generate(_markdown_with_n_lines(50))
    assert out == "auth bug investigation"
    assert runner.calls
    args = runner.calls[0]["args"]
    assert args[0] == "claude"
    assert "-p" in args
    # The prompt is the last arg
    assert "session" in args[-1].lower() or "summari" in args[-1].lower()


def test_generator_includes_head_and_tail_when_long(monkeypatch):
    runner = FakeRunner(FakeRun(stdout="long session summary\n"))
    gen = SmartSlugGenerator(runner=runner, head_lines=5, tail_lines=5)
    md = _markdown_with_n_lines(50)
    gen.generate(md)
    prompt = runner.calls[0]["args"][-1]
    assert "line 1" in prompt
    assert "line 5" in prompt
    assert "line 50" in prompt
    assert "line 46" in prompt
    assert "line 25" not in prompt  # middle should be omitted
    assert "truncated" in prompt.lower()


def test_generator_uses_full_text_when_short_enough():
    runner = FakeRunner(FakeRun(stdout="short summary\n"))
    gen = SmartSlugGenerator(runner=runner, head_lines=100, tail_lines=100)
    md = _markdown_with_n_lines(20)
    gen.generate(md)
    prompt = runner.calls[0]["args"][-1]
    # All 20 lines fit in the 100+100 window, so no "truncated" marker
    for i in (1, 10, 20):
        assert f"line {i}" in prompt
    assert "truncated" not in prompt.lower()


def test_generator_returns_none_on_nonzero_exit():
    runner = FakeRunner(FakeRun(returncode=1, stderr="oh no"))
    gen = SmartSlugGenerator(runner=runner)
    assert gen.generate("hi") is None


def test_generator_returns_none_when_claude_missing():
    runner = FakeRunner(FileNotFoundError("claude"))
    gen = SmartSlugGenerator(runner=runner)
    assert gen.generate("hi") is None


def test_generator_returns_none_on_timeout():
    runner = FakeRunner(subprocess.TimeoutExpired(cmd="claude", timeout=30))
    gen = SmartSlugGenerator(runner=runner)
    assert gen.generate("hi") is None


def test_generator_strips_quotes_and_punctuation():
    runner = FakeRunner(FakeRun(stdout='  "Auth Bug Investigation."  \n'))
    gen = SmartSlugGenerator(runner=runner)
    out = gen.generate("hi")
    assert out is not None
    # Surrounding whitespace and trailing period removed
    assert out == "Auth Bug Investigation"


def test_generator_returns_none_on_empty_output():
    runner = FakeRunner(FakeRun(stdout="\n"))
    gen = SmartSlugGenerator(runner=runner)
    assert gen.generate("hi") is None


def test_generator_passes_model_flag_when_set():
    runner = FakeRunner(FakeRun(stdout="ok\n"))
    gen = SmartSlugGenerator(runner=runner, model="claude-haiku-4-5")
    gen.generate("md")
    args = runner.calls[0]["args"]
    assert "--model" in args
    assert "claude-haiku-4-5" in args


def test_generator_passes_timeout_to_runner():
    runner = FakeRunner(FakeRun(stdout="ok\n"))
    gen = SmartSlugGenerator(runner=runner, timeout=42.0)
    gen.generate("md")
    assert runner.calls[0]["timeout"] == 42.0
