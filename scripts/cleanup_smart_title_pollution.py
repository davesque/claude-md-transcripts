"""
Clean up smart-title meta-sessions left behind by older versions of this tool.

Versions before 0.2.1 invoked ``claude -p`` without ``--no-session-persistence``,
so every smart-title call wrote a one-turn JSONL session under the project
directory it was invoked from. Running ``export-all`` later turned those
meta-sessions into noise transcripts in the output.

This script identifies meta-session JSONLs by their first user message
(which always begins with the smart-title prompt template) and, with
``--execute``, removes both the JSONLs and the corresponding rendered
markdown files.

Usage
-----

Dry-run (default) — prints what would be deleted, touches nothing::

    uv run python scripts/cleanup_smart_title_pollution.py

Actually delete::

    uv run python scripts/cleanup_smart_title_pollution.py --execute

Limit to one project::

    uv run python scripts/cleanup_smart_title_pollution.py --project mdsearch
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROMPT_PREFIX = "You are summarizing a Claude Code session transcript."

PROJECTS_ROOT = Path.home() / ".claude" / "projects"
OUTPUT_ROOT = Path.home() / ".claude" / "claude-md-transcripts"


@dataclass
class ProjectFinding:
    """
    Per-project pollution summary.
    """

    encoded_dir: Path
    basename: str
    polluted_jsonls: list[Path] = field(default_factory=list)
    polluted_mds: list[Path] = field(default_factory=list)


def is_meta_session(jsonl_path: Path) -> bool:
    """
    Return True if the first ``user`` record matches the smart-title prompt.

    Real session JSONLs typically begin with auxiliary lines (queue-operation,
    permission-mode, file-history-snapshot, system, etc.) before the first
    user prompt. We skip past those and inspect the first ``user`` record's
    message content.
    """
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    return False
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") != "user":
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    return False
                content = msg.get("content")
                text = _extract_text(content)
                return text.startswith(PROMPT_PREFIX)
    except OSError:
        return False
    return False


def _extract_text(content: object) -> str:
    """
    Pull a string out of message ``content`` regardless of shape.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    out.append(t)
        return "".join(out)
    return ""


def basename_for(encoded_dir: Path) -> str:
    """
    Mirror ``default_subdir_name`` for finding the matching output directory.
    """
    name = encoded_dir.name.lstrip("-")
    basename = name.rsplit("-", 1)[-1] if "-" in name else name
    return basename or "unknown"


def find_corresponding_md(jsonl: Path, output_dir: Path) -> Path | None:
    """
    Return the .md file matching this JSONL via the uuid8 suffix, if any.

    Filenames look like ``<date>_<slug>_<uuid8>.md`` where ``uuid8`` is the
    first 8 characters of the JSONL stem (which is the session UUID).
    """
    if not output_dir.exists():
        return None
    uuid8 = jsonl.stem.split("-", 1)[0][:8]
    if not uuid8:
        return None
    matches = list(output_dir.glob(f"*_{uuid8}.md"))
    return matches[0] if matches else None


def scan(project_filter: str | None = None) -> list[ProjectFinding]:
    """
    Scan ``~/.claude/projects/`` and report meta-session pollution per project.
    """
    if not PROJECTS_ROOT.exists():
        print(f"No Claude Code projects directory at {PROJECTS_ROOT}", file=sys.stderr)
        return []

    findings: list[ProjectFinding] = []
    for encoded_dir in sorted(p for p in PROJECTS_ROOT.iterdir() if p.is_dir()):
        basename = basename_for(encoded_dir)
        if project_filter and basename != project_filter:
            continue
        finding = ProjectFinding(encoded_dir=encoded_dir, basename=basename)
        output_dir = OUTPUT_ROOT / basename
        for jsonl in sorted(encoded_dir.glob("*.jsonl")):
            if is_meta_session(jsonl):
                finding.polluted_jsonls.append(jsonl)
                md = find_corresponding_md(jsonl, output_dir)
                if md is not None:
                    finding.polluted_mds.append(md)
        if finding.polluted_jsonls:
            findings.append(finding)
    return findings


def report(findings: list[ProjectFinding]) -> None:
    """
    Print a human-readable summary of the scan results.
    """
    if not findings:
        print("No smart-title meta-session pollution found.")
        return
    total_jsonls = sum(len(f.polluted_jsonls) for f in findings)
    total_mds = sum(len(f.polluted_mds) for f in findings)
    print(f"Found {total_jsonls} meta-session JSONLs and {total_mds} matching .md files")
    print(f"across {len(findings)} project(s):\n")
    for f in findings:
        print(f"  {f.basename} ({f.encoded_dir.name}):")
        print(f"    JSONLs: {len(f.polluted_jsonls)}")
        print(f"    .md files: {len(f.polluted_mds)}")


def execute(findings: list[ProjectFinding]) -> tuple[int, int]:
    """
    Actually delete the polluted files. Returns (jsonls_deleted, mds_deleted).
    """
    j_count = 0
    m_count = 0
    for f in findings:
        for j in f.polluted_jsonls:
            j.unlink()
            j_count += 1
        for m in f.polluted_mds:
            m.unlink()
            m_count += 1
    return j_count, m_count


def main(argv: list[str] | None = None) -> int:
    """
    Script entry point.
    """
    parser = argparse.ArgumentParser(
        description="Clean up smart-title meta-session pollution."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the polluted files (default: dry-run).",
    )
    parser.add_argument(
        "--project",
        help="Limit cleanup to a single project basename (e.g. 'mdsearch').",
    )
    parser.add_argument(
        "--list-paths",
        action="store_true",
        help="Print every polluted file path (JSONLs and matching .md files) "
        "grouped by project. Useful for review before --execute.",
    )
    args = parser.parse_args(argv)

    findings = scan(project_filter=args.project)
    if args.list_paths:
        for f in findings:
            print(f"# {f.basename} ({f.encoded_dir.name})")
            print(f"# JSONLs ({len(f.polluted_jsonls)}):")
            for j in f.polluted_jsonls:
                print(j)
            print(f"# .md files ({len(f.polluted_mds)}):")
            for m in f.polluted_mds:
                print(m)
            print()
        return 0
    report(findings)
    if not findings:
        return 0
    if not args.execute:
        print("\nDry run. Pass --execute to delete these files.")
        return 0
    j, m = execute(findings)
    print(f"\nDeleted {j} JSONLs and {m} .md files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
