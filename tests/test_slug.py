from pathlib import Path

from claude_md_transcripts.reader import read_session
from claude_md_transcripts.slug import (
    build_filename,
    fallback_slug_from_messages,
    pick_slug,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_session.jsonl"


def test_pick_slug_prefers_custom_title():
    result = read_session(FIXTURE)
    slug = pick_slug(result)
    # Fixture's customTitle is "Indexing Claude transcripts into qmd"
    assert "indexing" in slug.lower()
    assert "qmd" in slug.lower()


def test_pick_slug_falls_back_to_message_heuristic_when_no_title(tmp_path):
    # A session with no custom-title line; first user message becomes the slug.
    src = tmp_path / "x.jsonl"
    src.write_text(
        '{"type":"user","uuid":"a","parentUuid":null,"timestamp":"2026-01-01T00:00:00Z",'
        '"sessionId":"s","isSidechain":false,'
        '"message":{"role":"user","content":"How do I deploy the auth service?"}}\n'
    )
    result = read_session(src)
    slug = pick_slug(result)
    assert "deploy" in slug or "auth" in slug


def test_fallback_slug_combines_first_and_last_user_messages(tmp_path):
    src = tmp_path / "x.jsonl"
    src.write_text(
        '{"type":"user","uuid":"a","parentUuid":null,"timestamp":"2026-01-01T00:00:00Z",'
        '"sessionId":"s","isSidechain":false,'
        '"message":{"role":"user","content":"Investigate slow query in users table"}}\n'
        '{"type":"assistant","uuid":"b","parentUuid":"a","timestamp":"2026-01-01T00:00:01Z",'
        '"sessionId":"s","isSidechain":false,'
        '"message":{"model":"x","role":"assistant","content":[{"type":"text","text":"ok"}]}}\n'
        '{"type":"user","uuid":"c","parentUuid":"b","timestamp":"2026-01-01T00:00:02Z",'
        '"sessionId":"s","isSidechain":false,'
        '"message":{"role":"user","content":"Add an index on user_email"}}\n'
    )
    result = read_session(src)
    slug = fallback_slug_from_messages(result)
    # Should pull from both messages
    assert slug
    assert any(token in slug for token in ("query", "users", "index", "email"))


def test_fallback_slug_handles_empty_session(tmp_path):
    src = tmp_path / "empty.jsonl"
    src.write_text("")
    result = read_session(src)
    slug = pick_slug(result)
    assert slug == "untitled"


def test_build_filename_format():
    fname = build_filename(
        timestamp="2026-05-03T06:30:37.359Z",
        slug="some-thing-here",
        uuid="af6ff891-b945-426b-b678-18798e66b843",
    )
    assert fname.startswith("2026-05-03_")
    assert "some-thing-here" in fname
    assert fname.endswith("_af6ff891.md")


def test_build_filename_truncates_overlong_slug():
    fname = build_filename(
        timestamp="2026-05-03T06:30:37.359Z",
        slug="a" * 200,
        uuid="abc12345-0000-0000-0000-000000000000",
    )
    # Reasonable upper bound on filename length
    assert len(fname) < 120
