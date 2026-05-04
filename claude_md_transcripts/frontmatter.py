"""
Tiny parser for the markdown frontmatter format this project writes.

The format is intentionally minimal: a leading ``---`` line, key/value pairs
separated by ``:`` (no nesting, no quoting, no lists), terminated by a
closing ``---`` line. Using a hand-rolled parser keeps the dependency
surface small and the format predictable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DELIMITER = "---"


@dataclass
class Document:
    """
    A markdown document split into frontmatter fields and body.

    Attributes
    ----------
    fields
        Ordered key/value pairs from the frontmatter, preserving the order
        they appeared so re-serialization stays stable.
    body
        Everything after the closing ``---`` line.
    """

    fields: dict[str, str]
    body: str


def parse(text: str) -> Document:
    """
    Parse a markdown string into a :class:`Document`.

    If no frontmatter is present, ``fields`` is empty and ``body`` is the
    full input. The parser tolerates trailing whitespace on the delimiter
    lines but otherwise expects a strict ``---``-delimited block at the top.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != DELIMITER:
        return Document(fields={}, body=text)
    fields: dict[str, str] = {}
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == DELIMITER:
            end = i
            break
        line = lines[i]
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip()] = value.strip()
    if end == -1:
        # No closing delimiter; treat the whole thing as body.
        return Document(fields={}, body=text)
    body = "\n".join(lines[end + 1 :])
    if body and not body.endswith("\n"):
        body += "\n"
    if text.endswith("\n") and not body.endswith("\n"):
        body += "\n"
    return Document(fields=fields, body=body.lstrip("\n"))


def serialize(doc: Document) -> str:
    """
    Serialize a :class:`Document` back to a markdown string.

    Field order in the resulting frontmatter matches the order in
    ``doc.fields``.
    """
    if not doc.fields:
        return doc.body
    lines = [DELIMITER]
    for k, v in doc.fields.items():
        lines.append(f"{k}: {v}")
    lines.append(DELIMITER)
    head = "\n".join(lines) + "\n"
    body = doc.body
    if body and not body.startswith("\n"):
        head += "\n"
    return head + body


def replace_fields(text: str, **updates: Any) -> str:
    """
    Return ``text`` with the given frontmatter fields updated or appended.

    Convenience wrapper around :func:`parse` and :func:`serialize`.
    """
    doc = parse(text)
    for k, v in updates.items():
        doc.fields[k] = str(v)
    return serialize(doc)


def has_field(text: str, key: str, value: str | None = None) -> bool:
    """
    Test whether the frontmatter contains ``key`` (optionally with ``value``).
    """
    doc = parse(text)
    if key not in doc.fields:
        return False
    if value is None:
        return True
    return doc.fields[key] == value
