from claude_md_transcripts.frontmatter import (
    Document,
    has_field,
    parse,
    replace_fields,
    serialize,
)


def test_parse_simple_frontmatter():
    text = "---\nkey: value\nanother: thing\n---\nbody here\n"
    doc = parse(text)
    assert doc.fields == {"key": "value", "another": "thing"}
    assert doc.body.strip() == "body here"


def test_parse_no_frontmatter():
    text = "no frontmatter here\nsecond line\n"
    doc = parse(text)
    assert doc.fields == {}
    assert doc.body == text


def test_parse_unterminated_frontmatter():
    text = "---\nkey: value\nbody starts here without closing\n"
    doc = parse(text)
    assert doc.fields == {}
    assert doc.body == text


def test_serialize_roundtrip():
    doc = Document(fields={"a": "1", "b": "two"}, body="body content\n")
    out = serialize(doc)
    parsed = parse(out)
    assert parsed.fields == doc.fields
    assert parsed.body.strip() == "body content"


def test_replace_fields_updates_existing_and_appends_new():
    text = "---\nkey: old\n---\nbody\n"
    out = replace_fields(text, key="new", added="value")
    parsed = parse(out)
    assert parsed.fields == {"key": "new", "added": "value"}
    assert parsed.body.strip() == "body"


def test_has_field_detects_presence():
    text = "---\nflag: true\n---\nbody\n"
    assert has_field(text, "flag")
    assert has_field(text, "flag", "true")
    assert not has_field(text, "flag", "false")
    assert not has_field(text, "missing")


def test_serialize_handles_empty_body():
    doc = Document(fields={"a": "1"}, body="")
    out = serialize(doc)
    parsed = parse(out)
    assert parsed.fields == {"a": "1"}
