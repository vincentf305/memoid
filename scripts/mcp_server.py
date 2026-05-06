#!/usr/bin/env python3
import os
import datetime
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Memoid")

# Root directory of the Memoid repository
ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / "memory"
RAW_DIR = MEMORY_DIR / "raw"
WIKI_DIR = MEMORY_DIR / "wiki"
EVIDENCE_DIR = MEMORY_DIR / "evidence"
INDEX_PATH = WIKI_DIR / "INDEX.md"
TODO_MARKERS = ("TODO", "FIXME", "HACK", "XXX")
TODO_COMMENT_PATTERN = re.compile(
    r"(?P<prefix>#|//|/\*+|\*|<!--|;|--)\s*(?P<marker>TODO|FIXME|HACK|XXX)\b[:\-\s]*(?P<text>.*)",
    flags=re.IGNORECASE,
)
IGNORED_CODE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".mypy_cache",
    ".pytest_cache",
}
LOW_SIGNAL_TODO_SEGMENTS = {"test", "tests", "spec", "specs", "__tests__", "fixtures", "docs", "examples"}
MAX_TODO_FILE_BYTES = 1_000_000
INTERNAL_METADATA_KEYS = {
    "extract_todos",
    "extracted_todos",
    "generated_sections",
    "todo_scan_completed",
    "extract_remotes",
    "git_repo_info",
    "git_remotes",
    "git_remote_extraction_pending",
}


def _tokenize(text: str) -> List[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_./-]+", text.lower()) if token]


def _score_text(query_tokens: List[str], text: str, name: str = "") -> int:
    haystack = f"{name}\n{text}".lower()
    score = 0
    for token in query_tokens:
        if token in haystack:
            score += 1
    return score


def _extract_markdown_links(text: str) -> List[str]:
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)


def _resolve_repo_relative(markdown_target: str, base_dir: Path) -> Optional[Path]:
    target = markdown_target.strip()
    if not target or target.startswith(("http://", "https://", "mailto:", "#")):
        return None

    target = target.split("#", 1)[0]
    candidate = (base_dir / target).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return None
    return candidate


def _find_index_candidates(query_tokens: List[str], limit: int) -> List[Path]:
    if not INDEX_PATH.exists():
        return []

    index_text = INDEX_PATH.read_text(encoding="utf-8")
    candidates: Dict[Path, int] = {}
    for line in index_text.splitlines():
        line_links = _extract_markdown_links(line)
        if not line_links:
            continue
        for target in line_links:
            resolved = _resolve_repo_relative(target, INDEX_PATH.parent)
            if not resolved or not resolved.is_file() or resolved.suffix != ".md":
                continue
            score = _score_text(query_tokens, line, resolved.name)
            if score > 0:
                candidates[resolved] = max(candidates.get(resolved, 0), score)

    if not candidates:
        return []

    ranked = sorted(
        candidates.items(),
        key=lambda item: (-item[1], item[0].name.lower()),
    )
    return [path for path, _ in ranked[:limit]]


def _rank_wiki_pages(query_tokens: List[str], seed_pages: List[Path], limit: int) -> List[Path]:
    ranked: Dict[Path, int] = {}
    visited: Set[Path] = set()

    for page in seed_pages:
        if page in visited or not page.is_file():
            continue
        visited.add(page)
        content = page.read_text(encoding="utf-8")
        score = _score_text(query_tokens, content, page.name)
        ranked[page] = max(ranked.get(page, 0), score + 5)

        for target in _extract_markdown_links(content):
            resolved = _resolve_repo_relative(target, page.parent)
            if not resolved or not resolved.is_file() or resolved.suffix != ".md":
                continue
            try:
                resolved.relative_to(WIKI_DIR)
            except ValueError:
                continue
            linked_content = resolved.read_text(encoding="utf-8")
            linked_score = _score_text(query_tokens, linked_content, resolved.name)
            if linked_score > 0:
                ranked[resolved] = max(ranked.get(resolved, 0), linked_score)

    if not ranked:
        return []

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0].name.lower()))
    return [path for path, _ in ordered[:limit]]


def _collect_linked_evidence(query_tokens: List[str], wiki_pages: List[Path], limit: int) -> List[Path]:
    ranked: Dict[Path, int] = {}

    for page in wiki_pages:
        content = page.read_text(encoding="utf-8")
        for target in _extract_markdown_links(content):
            resolved = _resolve_repo_relative(target, page.parent)
            if not resolved or not resolved.is_file() or resolved.suffix != ".md":
                continue
            try:
                resolved.relative_to(EVIDENCE_DIR)
            except ValueError:
                continue
            evidence_content = resolved.read_text(encoding="utf-8")
            score = _score_text(query_tokens, evidence_content, resolved.name)
            if score > 0:
                ranked[resolved] = max(ranked.get(resolved, 0), score)

    if not ranked:
        return []

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0].name.lower()))
    return [path for path, _ in ordered[:limit]]


def _collect_linked_raw(query_tokens: List[str], evidence_pages: List[Path], limit: int) -> List[Path]:
    ranked: Dict[Path, int] = {}

    for page in evidence_pages:
        content = page.read_text(encoding="utf-8")
        for target in _extract_markdown_links(content):
            resolved = _resolve_repo_relative(target, page.parent)
            if not resolved or not resolved.is_file():
                continue
            try:
                resolved.relative_to(RAW_DIR)
            except ValueError:
                continue
            raw_content = resolved.read_text(encoding="utf-8")
            score = _score_text(query_tokens, raw_content, resolved.name)
            if score > 0:
                ranked[resolved] = max(ranked.get(resolved, 0), score)

    if not ranked:
        return []

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0].name.lower()))
    return [path for path, _ in ordered[:limit]]


def _render_section(title: str, files: List[Path]) -> str:
    if not files:
        return f"## {title}\n- None"
    lines = [f"## {title}"]
    for file_path in files:
        lines.append(f"- {file_path.relative_to(ROOT)}")
    return "\n".join(lines)


def _find_relevant_chunks(content: str, query_tokens: List[str], max_chunks: int = 3, context_window: int = 300) -> List[str]:
    """Extract bounded excerpts around query term matches. Returns deduplicated chunks."""
    if not query_tokens:
        lines = content.splitlines()
        return ["\n".join(lines[:15])] if len(lines) > 15 else [content[:context_window]]

    content_lower = content.lower()
    chunks: List[str] = []
    seen_ranges: Set[tuple] = set()

    for token in query_tokens:
        pos = 0
        while True:
            idx = content_lower.find(token, pos)
            if idx == -1:
                break
            start = max(0, idx - context_window // 2)
            end = min(len(content), idx + context_window // 2)
            # Expand to nearest newline boundaries
            while start > 0 and content[start] != '\n':
                start -= 1
            while end < len(content) and content[end] != '\n':
                end += 1
            chunk_range = (start, end)
            if chunk_range not in seen_ranges:
                seen_ranges.add(chunk_range)
                excerpt = content[start:end].strip()
                if excerpt:
                    chunks.append(excerpt)
                if len(chunks) >= max_chunks:
                    break
            pos = idx + len(token)
        if len(chunks) >= max_chunks:
            break

    if not chunks:
        lines = content.splitlines()
        return ["\n".join(lines[:10])]
    return chunks


def _render_bounded_excerpts(title: str, files: List[Path], query_tokens: Optional[List[str]] = None, max_chunks_per_file: int = 3) -> str:
    if not files:
        return ""
    parts = [f"## {title}"]
    for file_path in files:
        content = file_path.read_text(encoding="utf-8")
        # Extract current section heading context
        heading_context = ""
        for line in content.splitlines():
            if line.startswith("## "):
                heading_context = line
        heading_note = f" {heading_context}" if heading_context else ""
        parts.append(f"--- File: {file_path.relative_to(ROOT)}{heading_note} ---")

        if query_tokens:
            chunks = _find_relevant_chunks(content, query_tokens, max_chunks_per_file)
            for idx, chunk in enumerate(chunks):
                parts.append(f"[Excerpt {idx + 1}]\n{chunk}")
            total_chars = sum(len(c) for c in chunks)
            if total_chars < len(content) * 0.8:
                parts.append(f"[Truncated: {len(content) - total_chars} chars omitted]")
        else:
            # No query tokens: return first 500 chars as summary
            parts.append(content[:500].strip())
            if len(content) > 500:
                parts.append(f"[Truncated: {len(content) - 500} chars omitted]")
        parts.append("")
    return "\n".join(parts).rstrip()


def _render_file_dump(title: str, files: List[Path]) -> str:
    """Legacy full dump — only for small files or explicit raw source requests."""
    if not files:
        return ""
    parts = [f"## {title}"]
    for file_path in files:
        content = file_path.read_text(encoding="utf-8")
        parts.append(f"--- File: {file_path.relative_to(ROOT)} ---\n{content}\n")
    return "\n".join(parts).rstrip()


def _is_probably_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            sample = f.read(8192)
    except OSError:
        return False
    return b"\x00" not in sample


def _infer_code_context(lines: List[str], line_index: int) -> str:
    definition_patterns = [
        re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
        re.compile(r"^\s*(?:func|function)\s+([A-Za-z_][A-Za-z0-9_]*)"),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*="),
        re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_:<>]*)\s*\([^)]*\)\s*\{?\s*$"),
    ]
    for offset in range(line_index - 1, max(-1, line_index - 8), -1):
        candidate = lines[offset].strip()
        if not candidate:
            continue
        for pattern in definition_patterns:
            match = pattern.match(candidate)
            if match:
                return match.group(1)
        if not candidate.startswith(("#", "//", "/*", "*", "<!--", ";", "--")):
            return candidate[:120]
    return ""


def _extract_todo_items_from_text(content: str, file_path: Path, base_path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    lines = content.splitlines()
    rel_path = file_path.relative_to(base_path).as_posix()
    for index, line in enumerate(lines):
        match = TODO_COMMENT_PATTERN.search(line)
        if not match:
            continue
        marker = match.group("marker").upper()
        text = match.group("text").strip() or "(no detail)"
        context = _infer_code_context(lines, index)
        items.append(
            {
                "marker": marker,
                "text": text,
                "path": rel_path,
                "line": index + 1,
                "context": context,
                "low_signal": any(segment in LOW_SIGNAL_TODO_SEGMENTS for segment in Path(rel_path).parts),
            }
        )
    return items


def _extract_code_todos(codebase_path: Path) -> List[Dict[str, Any]]:
    if not codebase_path.exists() or not codebase_path.is_dir():
        return []

    items: List[Dict[str, Any]] = []
    for path in sorted(codebase_path.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_CODE_DIRS for part in path.relative_to(codebase_path).parts):
            continue
        try:
            if path.stat().st_size > MAX_TODO_FILE_BYTES or not _is_probably_text_file(path):
                continue
        except OSError:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        except OSError:
            continue
        items.extend(_extract_todo_items_from_text(content, path, codebase_path))
    return items


def _summarize_todo_markers(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "none"
    counts = Counter(item["marker"] for item in items)
    return ", ".join(f"{marker}: {counts[marker]}" for marker in TODO_MARKERS if counts.get(marker))


def _render_todo_lines(items: List[Dict[str, Any]], limit: Optional[int] = None) -> List[str]:
    lines: List[str] = []
    selected = items if limit is None else items[:limit]
    for item in selected:
        context = f" (`{item['context']}`)" if item.get("context") else ""
        lines.append(
            f"- `{item['marker']}` in `{item['path']}:{item['line']}`{context}: {item['text']}"
        )
    return lines


def _promote_todo_items(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    promoted = [item for item in items if not item.get("low_signal")]
    if len(promoted) < limit:
        fallback = [item for item in items if item not in promoted]
        promoted.extend(fallback[: limit - len(promoted)])
    return promoted[:limit]


def _path_is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _detect_git_repo(codebase_path: Path) -> Optional[Dict[str, Any]]:
    try:
        top_level = subprocess.run(
            ["git", "-C", codebase_path.as_posix(), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    repo_root = Path(top_level).resolve()
    try:
        branch = subprocess.run(
            ["git", "-C", repo_root.as_posix(), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        branch = "unknown"

    remotes: List[Dict[str, str]] = []
    try:
        remote_names_output = subprocess.run(
            ["git", "-C", repo_root.as_posix(), "remote"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        remote_names = [name.strip() for name in remote_names_output.splitlines() if name.strip()]
        for remote_name in remote_names:
            try:
                remote_url = subprocess.run(
                    ["git", "-C", repo_root.as_posix(), "remote", "get-url", remote_name],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            except subprocess.CalledProcessError:
                continue
            remotes.append({"name": remote_name, "url": remote_url})
    except subprocess.CalledProcessError:
        pass

    return {
        "repo_root": repo_root.as_posix(),
        "branch": branch,
        "remote_count": len(remotes),
        "remotes": remotes,
    }


def _render_git_remote_lines(remotes: List[Dict[str, str]]) -> List[str]:
    return [f"- `{remote['name']}`: `{remote['url']}`" for remote in remotes]


def _apply_generated_sections(
    content: str,
    generated_sections: Dict[str, str],
    preferred_order: Optional[List[str]] = None,
) -> str:
    if not generated_sections:
        return content
    title, sections, order = _parse_markdown_sections(content)
    if not title:
        return content

    preferred_order = preferred_order or []
    for heading in preferred_order:
        if heading in generated_sections and heading not in order:
            if "## Sources" in order:
                order.insert(order.index("## Sources"), heading)
            else:
                order.append(heading)

    for heading, body in generated_sections.items():
        sections[heading] = body.strip()
        if heading not in order:
            if "## Sources" in order:
                order.insert(order.index("## Sources"), heading)
            else:
                order.append(heading)

    return _render_markdown_page(title, sections, order)


def _path_is_indexed(page_path: Path) -> bool:
    if not INDEX_PATH.exists():
        return False
    needle = f"(./{page_path.relative_to(WIKI_DIR).as_posix()})"
    return needle in INDEX_PATH.read_text(encoding="utf-8")


def _page_trust_report(page_path: Path) -> Dict[str, Any]:
    content = page_path.read_text(encoding="utf-8")
    links = [_resolve_repo_relative(target, page_path.parent) for target in _extract_markdown_links(content)]
    evidence_links = [link for link in links if link and link.is_file() and link.is_relative_to(EVIDENCE_DIR)]
    raw_links = [link for link in links if link and link.is_file() and link.is_relative_to(RAW_DIR)]
    issues: List[str] = []

    if not _path_is_indexed(page_path):
        issues.append("not indexed")
    if "## Sources" not in content:
        issues.append("missing sources section")
    if not evidence_links:
        issues.append("no evidence links")
    if page_path.parent.name == "entities":
        for required in ("## Current", "## History"):
            if required not in content:
                issues.append(f"missing {required}")

    score = 100
    score -= min(len(issues) * 20, 80)
    if raw_links:
        score += 5
    score = max(5, min(score, 100))

    if score >= 85:
        confidence = "high"
    elif score >= 60:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "path": page_path.relative_to(ROOT).as_posix(),
        "indexed": _path_is_indexed(page_path),
        "evidence_links": len(evidence_links),
        "raw_links": len(raw_links),
        "confidence": confidence,
        "issues": issues,
    }


def _render_trust_section(wiki_pages: List[Path]) -> str:
    lines = ["## Trust Signals"]
    if not wiki_pages:
        lines.append("- No wiki pages retrieved.")
        return "\n".join(lines)
    for page in wiki_pages:
        report = _page_trust_report(page)
        issue_text = ", ".join(report["issues"]) if report["issues"] else "none"
        lines.append(
            f"- `{report['path']}`: confidence={report['confidence']}, indexed={'yes' if report['indexed'] else 'no'}, "
            f"evidence_links={report['evidence_links']}, raw_links={report['raw_links']}, issues={issue_text}"
        )
    return "\n".join(lines)


def _scoped_lint_report(page_paths: List[Path]) -> str:
    findings = _lint_modified_pages(page_paths)
    if not findings:
        return "Scoped Lint: clean"
    return "Scoped Lint:\n" + "\n".join(findings)


def _read_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _wake_up_summary(identity_text: str, story_text: str, next_reads: List[Path]) -> str:
    active_threads: List[str] = []
    in_active = False
    for line in story_text.splitlines():
        if line.strip() == "## Active Threads":
            in_active = True
            continue
        if in_active and line.startswith("## "):
            break
        if in_active and line.strip().startswith("- "):
            active_threads.append(line.strip()[2:])

    open_questions: List[str] = []
    in_questions = False
    for line in story_text.splitlines():
        if line.strip() == "## Open Questions":
            in_questions = True
            continue
        if in_questions and line.startswith("## "):
            break
        if in_questions and line.strip().startswith("- "):
            open_questions.append(line.strip()[2:])

    lines = [
        "## Wake-Up Summary",
        f"- System purpose: {'Memoid is a markdown-first memory system for an AI agent.' if identity_text else 'Unavailable'}",
        f"- Active threads: {('; '.join(active_threads[:3])) if active_threads else 'None listed'}",
        f"- Open questions: {('; '.join(open_questions[:3])) if open_questions else 'None listed'}",
        f"- Read next: {(', '.join(path.relative_to(ROOT).as_posix() for path in next_reads)) if next_reads else 'Use memoid_recall for task-specific retrieval'}",
    ]
    return "\n".join(lines)


def _wake_up_next_reads(query: Optional[str], limit: int = 2) -> List[Path]:
    if not query:
        return []
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    candidates = _find_index_candidates(query_tokens, limit)
    return [path for path in candidates if path not in {WIKI_DIR / "IDENTITY.md", WIKI_DIR / "ESSENTIAL_STORY.md"}][:limit]


@mcp.tool()
def memoid_wake_up(query: Optional[str] = None, include_index: bool = True, next_read_limit: int = 2) -> str:
    """
    Reconstructs bounded Memoid state for outside-repo use.
    Returns IDENTITY.md and ESSENTIAL_STORY.md, optionally INDEX.md, plus a small next-read hint set.
    """
    identity_path = WIKI_DIR / "IDENTITY.md"
    story_path = WIKI_DIR / "ESSENTIAL_STORY.md"
    next_reads = _wake_up_next_reads(query, next_read_limit)

    identity_text = _read_if_exists(identity_path)
    story_text = _read_if_exists(story_path)
    index_text = _read_if_exists(INDEX_PATH) if include_index else ""

    sections = [
        "# Wake-Up Context",
        _wake_up_summary(identity_text, story_text, next_reads),
    ]
    if include_index and next_reads and not query:
        sections.append(
            "## Suggested Next Reads\n" + "\n".join(f"- {path.relative_to(ROOT).as_posix()}" for path in next_reads)
        )

    detail_sections = [
        f"## Identity\n{identity_text}",
        f"## Essential Story\n{story_text}",
    ]
    if include_index:
        detail_sections.append(f"## Index\n{index_text}")
    if next_reads:
        detail_sections.append(_render_bounded_excerpts("Suggested Page Content", next_reads))

    return "\n\n".join(sections + detail_sections)

@mcp.tool()
def memoid_recall(query: str, limit: int = 10, allow_raw: bool = False) -> str:
    """
    Retrieves Memoid context using the retrieval ladder:
    index -> relevant wiki pages -> linked evidence pages -> raw sources.
    Raw sources are only consulted when allow_raw=True.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return "Query must contain at least one searchable token."

    index_candidates = _find_index_candidates(query_tokens, limit)
    wiki_pages = _rank_wiki_pages(query_tokens, index_candidates, limit)
    evidence_pages = _collect_linked_evidence(query_tokens, wiki_pages, limit)
    raw_files = _collect_linked_raw(query_tokens, evidence_pages, limit) if allow_raw else []

    if not index_candidates and not wiki_pages and not evidence_pages and not raw_files:
        return f"No results found for query: '{query}'"

    sections = [
        f"# Retrieval Results for: {query}",
        _render_section("Index Hits", index_candidates),
        _render_section("Wiki Pages Used", wiki_pages),
        _render_section("Evidence Pages Used", evidence_pages),
        _render_trust_section(wiki_pages),
    ]

    if allow_raw:
        sections.append(_render_section("Raw Sources Used", raw_files))
    else:
        sections.append("## Raw Sources Used\n- Skipped (`allow_raw=False`)")

    detail_sections = [
        _render_bounded_excerpts("Wiki Content", wiki_pages, query_tokens),
        _render_bounded_excerpts("Evidence Content", evidence_pages, query_tokens),
    ]
    if allow_raw:
        detail_sections.append(_render_file_dump("Raw Content", raw_files))

    return "\n\n".join(sections + [section for section in detail_sections if section])


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "untitled"


def _format_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    if not metadata:
        return ""
    lines = []
    for key, value in metadata.items():
        if key in INTERNAL_METADATA_KEYS:
            continue
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)


def _relative_link(from_dir: Path, to_path: Path) -> str:
    return os.path.relpath(to_path, start=from_dir).replace(os.sep, "/")


def _default_wiki_path(source_name: str, metadata: Optional[Dict[str, Any]]) -> str:
    page_type = "concepts"
    if metadata:
        candidate = str(metadata.get("page_type", "")).strip().lower()
        if candidate in {"entities", "concepts", "domains", "comparisons", "syntheses"}:
            page_type = candidate
    return f"{page_type}/{_slugify(source_name)}.md"


def _section_template(page_type: str) -> List[str]:
    templates = {
        "entities": ["## Summary", "## Current", "## History", "## Relationships", "## Sources"],
        "concepts": ["## Summary", "## Key Ideas", "## Variants", "## Tradeoffs", "## Sources"],
        "domains": ["## Summary", "## Current", "## Relationships", "## Sources"],
        "comparisons": ["## Summary", "## Criteria", "## Tradeoffs", "## Sources"],
        "syntheses": ["## Summary", "## Key Ideas", "## Sources"],
    }
    return templates.get(page_type, ["## Summary", "## Notes", "## Sources"])


def _evidence_section_template(category: str) -> List[str]:
    templates = {
        "session": ["## Context", "## Events", "## Findings", "## Decisions", "## Follow-ups", "## Affected Pages"],
        "decision": ["## Decision", "## Date", "## Context", "## Rationale", "## Alternatives", "## Consequences", "## Sources"],
        "audit": ["## Scope", "## Findings", "## Follow-ups", "## Affected Pages", "## Sources"],
    }
    return templates.get(category, ["## Context", "## Events", "## Findings", "## Decisions", "## Follow-ups", "## Affected Pages"])


def _guess_page_type(page_path: str) -> str:
    path = Path(page_path)
    if path.parts:
        first = path.parts[0].lower()
        if first in {"entities", "concepts", "domains", "comparisons", "syntheses"}:
            return first
    return "concepts"


def _ensure_sources_section(content: str) -> str:
    if re.search(r"^## Sources\s*$", content, flags=re.MULTILINE):
        return content
    content = content.rstrip()
    if content:
        content += "\n\n## Sources\n"
    else:
        content = "## Sources\n"
    return content


def _normalize_md_path(path_str: str) -> str:
    normalized = Path(path_str)
    if normalized.suffix != ".md":
        normalized = normalized.with_suffix(".md")
    return normalized.as_posix()


def _parse_markdown_sections(content: str) -> (str, Dict[str, str], List[str]):
    lines = content.splitlines()
    title = ""
    sections: Dict[str, List[str]] = {}
    order: List[str] = []
    current_section: Optional[str] = None
    preamble: List[str] = []

    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            current_section = line
            if current_section not in sections:
                sections[current_section] = []
                order.append(current_section)
            continue
        if current_section is None:
            preamble.append(line)
        else:
            sections[current_section].append(line)

    if preamble:
        sections["__preamble__"] = preamble

    rendered_sections = {name: "\n".join(body).strip() for name, body in sections.items()}
    return title, rendered_sections, order


def _render_markdown_page(title: str, sections: Dict[str, str], order: List[str]) -> str:
    parts = [f"# {title}", ""]
    preamble = sections.get("__preamble__", "").strip()
    if preamble:
        parts.extend([preamble, ""])
    for heading in order:
        if heading == "__preamble__":
            continue
        parts.append(heading)
        body = sections.get(heading, "").strip()
        parts.append(body if body else "_TBD_")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _merge_block(existing: str, new_text: str, mode: str = "append") -> str:
    existing = existing.strip()
    new_text = new_text.strip()
    if not existing:
        return new_text
    if not new_text:
        return existing
    if mode == "replace":
        return new_text
    if new_text in existing:
        return existing
    return f"{existing}\n\n{new_text}"


def _merge_list_block(existing: str, items: List[str]) -> str:
    existing_lines = [line.rstrip() for line in existing.splitlines() if line.strip()]
    seen = set(existing_lines)
    for item in items:
        if item and item not in seen:
            existing_lines.append(item)
            seen.add(item)
    return "\n".join(existing_lines).strip()


def _ensure_named_sections(sections: Dict[str, str], order: List[str], expected: List[str]) -> None:
    for heading in expected:
        if heading not in sections:
            sections[heading] = ""
        if heading not in order:
            order.append(heading)


def _lint_modified_pages(page_paths: List[Path]) -> List[str]:
    findings: List[str] = []
    for page in page_paths:
        if not page.exists() or page.suffix != ".md":
            findings.append(f"- Missing page: `{page.relative_to(ROOT).as_posix()}`")
            continue
        content = page.read_text(encoding="utf-8")
        requires_sources = (
            page.is_relative_to(WIKI_DIR)
            or page.parent.name in {"source-notes", "decisions", "audits"}
        )
        if requires_sources and not re.search(r"^## Sources\s*$", content, flags=re.MULTILINE):
            findings.append(f"- Missing `## Sources` section: `{page.relative_to(ROOT).as_posix()}`")
        if page.is_relative_to(WIKI_DIR):
            if not _extract_markdown_links(content):
                findings.append(f"- No linked evidence or related pages: `{page.relative_to(ROOT).as_posix()}`")
            if page.parent.name == "entities":
                for required in ("## Current", "## History", "## Sources"):
                    if required not in content:
                        findings.append(f"- Entity page missing `{required}`: `{page.relative_to(ROOT).as_posix()}`")
        if page.is_relative_to(EVIDENCE_DIR) and "## Affected Pages" not in content and page.parent.name in {"sessions", "audits"}:
            findings.append(f"- Evidence page missing `## Affected Pages`: `{page.relative_to(ROOT).as_posix()}`")
    return findings


def _append_source_link(content: str, page_dir: Path, source_note_path: Path) -> str:
    content = _ensure_sources_section(content)
    link = f"- [{source_note_path.stem}]({_relative_link(page_dir, source_note_path)})"
    if link in content:
        return content
    return content.rstrip() + f"\n{link}\n"


def _build_wiki_page(
    page_path: str,
    source_name: str,
    target_path: Path,
    source_note_path: Path,
    summary: Optional[str],
    metadata: Optional[Dict[str, Any]],
    existing_content: Optional[str] = None,
) -> str:
    page_type = _guess_page_type(page_path)
    generated_sections = {}
    if metadata:
        generated_sections = {
            key: str(value).strip()
            for key, value in metadata.get("generated_sections", {}).items()
            if str(value).strip()
        }
    if existing_content is not None:
        content = existing_content.rstrip()
        if summary and not re.search(r"^## Summary\s*$", content, flags=re.MULTILINE):
            content = f"# {source_name}\n\n## Summary\n{summary}\n\n{content}".strip()
        content = _apply_generated_sections(content, generated_sections, preferred_order=["## Open TODOs"])
        return _append_source_link(content, target_path.parent, source_note_path)

    sections = _section_template(page_type)
    lines = [f"# {source_name}", ""]
    rendered_summary = summary or str(metadata.get("summary", "")).strip() if metadata else ""

    for section in sections:
        lines.append(section)
        if section == "## Summary" and rendered_summary:
            lines.append(rendered_summary)
        elif section == "## Sources":
            lines.append(f"- [{source_note_path.stem}]({_relative_link(target_path.parent, source_note_path)})")
        else:
            lines.append("_TBD_")
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    return _apply_generated_sections(content, generated_sections, preferred_order=["## Open TODOs"])


def _ensure_index_entry(page_path: str, title: str, summary: str) -> bool:
    if not INDEX_PATH.exists():
        return False

    index_text = INDEX_PATH.read_text(encoding="utf-8")
    page_path_obj = Path(page_path)
    section_name = _guess_page_type(page_path)
    heading = f"## {section_name.capitalize()}"
    entry = f"- [{page_path_obj.name}](./{page_path_obj.as_posix()}): {summary or title}"

    if f"(./{page_path_obj.as_posix()})" in index_text:
        return False

    lines = index_text.splitlines()
    insert_at = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            insert_at = idx + 1
            while insert_at < len(lines) and lines[insert_at].startswith("- "):
                insert_at += 1
            break

    if insert_at is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend([heading, "", entry])
    else:
        while insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1
        lines.insert(insert_at, entry)

    INDEX_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def _build_source_note(
    source_name: str,
    timestamp: str,
    raw_file: Path,
    source_note_path: Path,
    summary: str,
    metadata: Optional[Dict[str, Any]],
    affected_pages: List[str],
) -> str:
    meta_block = _format_metadata(metadata)
    affected = "\n".join(
        f"- [memory/wiki/{page}]({_relative_link(source_note_path.parent, WIKI_DIR / page)})"
        for page in affected_pages
    ) or "- None yet"
    lines = [
        f"# Source Note: {source_name}",
        "",
        f"- **Ingested At**: {timestamp}",
        f"- **Raw Path**: {raw_file.relative_to(ROOT).as_posix()}",
    ]
    if meta_block:
        lines.extend(["", "## Metadata", meta_block])
    lines.extend(
        [
            "",
            "## Summary",
            summary,
        "",
            "## Affected Pages",
            affected,
        "",
            "## Sources",
            f"- [raw source]({_relative_link(source_note_path.parent, raw_file)})",
        ]
    )
    todo_scan_completed = bool(metadata.get("todo_scan_completed")) if metadata else False
    extracted_todos = metadata.get("extracted_todos") if metadata else None
    if todo_scan_completed:
        extracted_todos = extracted_todos or []
        total_count = len(extracted_todos)
        note_lines = _render_todo_lines(extracted_todos, limit=50) or ["- None found."]
        if extracted_todos and total_count > len(note_lines):
            note_lines.append(f"- ... {total_count - len(note_lines)} more TODO item(s) omitted from this note")
        lines[lines.index("## Sources"):lines.index("## Sources")] = [
            "",
            "## Extracted TODOs",
            f"- Total TODO-style comments found: {total_count}",
            f"- Marker breakdown: {_summarize_todo_markers(extracted_todos)}",
            *note_lines,
            "",
        ]
    git_repo_info = metadata.get("git_repo_info") if metadata else None
    if git_repo_info:
        git_section = [
            "",
            "## Source Control",
            f"- Git repository detected: yes",
            f"- Repo root: `{git_repo_info['repo_root']}`",
            f"- Active branch: `{git_repo_info['branch']}`",
        ]
        if metadata.get("git_remotes"):
            git_section.extend(
                [
                    f"- Remote count: {len(metadata['git_remotes'])}",
                    "",
                    "### Remotes",
                    *_render_git_remote_lines(metadata["git_remotes"]),
                ]
            )
        elif metadata.get("git_remote_extraction_pending"):
            git_section.append("- Remotes: pending user approval before extraction")
        else:
            git_section.append("- Remotes: not extracted")
        lines[lines.index("## Sources"):lines.index("## Sources")] = git_section + [""]
    return "\n".join(lines).rstrip() + "\n"


def _link_lines(from_dir: Path, targets: List[Path], label_prefix: str = "") -> List[str]:
    lines: List[str] = []
    for target in targets:
        rel = _relative_link(from_dir, target)
        label = label_prefix + target.name
        lines.append(f"- [{label}]({rel})")
    return lines


def _append_log_entry(message: str) -> None:
    with open(WIKI_DIR / "LOG.md", "a", encoding="utf-8") as f:
        f.write(f"\n{message}")


def _recent_log_paths(limit: int = 5) -> List[Path]:
    if not (WIKI_DIR / "LOG.md").exists():
        return []
    log_text = (WIKI_DIR / "LOG.md").read_text(encoding="utf-8")
    matches = re.findall(r"`(memory/wiki/[^`]+\.md)`", log_text)
    paths: List[Path] = []
    seen: Set[Path] = set()
    for raw in reversed(matches):
        candidate = ROOT / raw
        if candidate.exists() and candidate not in seen:
            seen.add(candidate)
            paths.append(candidate)
        if len(paths) >= limit:
            break
    return list(reversed(paths))


def _orphan_wiki_pages() -> List[Path]:
    if not INDEX_PATH.exists():
        return []
    index_text = INDEX_PATH.read_text(encoding="utf-8")
    indexed = set()
    for target in _extract_markdown_links(index_text):
        resolved = _resolve_repo_relative(target, INDEX_PATH.parent)
        if resolved and resolved.is_file() and resolved.suffix == ".md" and resolved.is_relative_to(WIKI_DIR):
            indexed.add(resolved)
    all_pages = [path for path in WIKI_DIR.glob("**/*.md") if path.name not in {"INDEX.md", "LOG.md", "IDENTITY.md", "ESSENTIAL_STORY.md"}]
    return sorted([path for path in all_pages if path not in indexed], key=lambda p: p.as_posix())


def _build_audit_note(scope: str, targets: List[Path], findings: List[str], follow_ups: List[str], audit_path: Path) -> str:
    title = f"Audit: {audit_path.stem}"
    sections = {
        "## Scope": f"Mode: {scope}\n\nTargets:\n" + ("\n".join(f"- `{path.relative_to(ROOT).as_posix()}`" for path in targets) if targets else "- None"),
        "## Findings": "\n".join(findings) if findings else "- No findings.",
        "## Follow-ups": "\n".join(f"- {item}" for item in follow_ups) if follow_ups else "- None.",
        "## Affected Pages": "\n".join(
            f"- [memory/wiki/{path.relative_to(WIKI_DIR).as_posix()}]({_relative_link(audit_path.parent, path)})"
            for path in targets
            if path.is_relative_to(WIKI_DIR)
        ) or "- None.",
        "## Sources": f"- [memory/wiki/LOG.md]({_relative_link(audit_path.parent, WIKI_DIR / 'LOG.md')})",
    }
    order = _evidence_section_template("audit")
    return _render_markdown_page(title, sections, order)

@mcp.tool()
def memoid_ingest(
    content: str,
    source_name: str,
    metadata: Optional[Dict[str, Any]] = None,
    summary: Optional[str] = None,
    wiki_page_path: Optional[str] = None,
) -> str:
    """
    Ingests new content into Memoid using the standard pipeline:
    raw source -> source note -> wiki update -> index update -> log entry.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_name = _slugify(source_name)
    metadata = dict(metadata or {})

    codebase_path_value = str(metadata.get("codebase_path", "")).strip()
    extracted_todos: List[Dict[str, Any]] = []
    codebase_path: Optional[Path] = None
    if codebase_path_value and str(metadata.get("extract_todos", "true")).lower() != "false":
        codebase_path = Path(codebase_path_value).expanduser()
        if codebase_path.exists() and codebase_path.is_dir():
            extracted_todos = _extract_code_todos(codebase_path.resolve())
            metadata["codebase_path"] = codebase_path.resolve().as_posix()
            metadata["todo_count"] = len(extracted_todos)
            metadata["todo_markers"] = _summarize_todo_markers(extracted_todos)
            metadata["todo_scan_completed"] = True
        else:
            metadata["codebase_path"] = codebase_path.as_posix()
            metadata["todo_extraction_warning"] = "codebase_path was not a readable directory"
    elif codebase_path_value:
        codebase_path = Path(codebase_path_value).expanduser()
        metadata["codebase_path"] = codebase_path.as_posix()

    git_repo_info: Optional[Dict[str, Any]] = None
    remote_prompt = ""
    if codebase_path and codebase_path.exists() and codebase_path.is_dir():
        resolved_codebase_path = codebase_path.resolve()
        if not _path_is_within(ROOT, resolved_codebase_path):
            git_repo_info = _detect_git_repo(resolved_codebase_path)
            if git_repo_info:
                metadata["git_repo_info"] = git_repo_info
                extract_remotes_mode = str(metadata.get("extract_remotes", "ask")).lower()
                if extract_remotes_mode in {"true", "yes"}:
                    metadata["git_remotes"] = git_repo_info["remotes"]
                elif git_repo_info["remotes"]:
                    metadata["git_remote_extraction_pending"] = True
                    remote_prompt = (
                        "Git remotes detected for the ingested external repo. "
                        "Ask the user whether to extract and store them, then rerun ingest with "
                        "`metadata.extract_remotes=true` to add them to the entity and source note."
                    )

    # 1. Save Raw Source
    raw_file = RAW_DIR / "inbox" / f"{safe_name}.md"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(content, encoding="utf-8")

    # 2. Create or update a wiki page so ingest always leaves durable synthesis behind.
    target_page = wiki_page_path or _default_wiki_path(source_name, metadata)
    target_path = WIKI_DIR / target_page
    if target_path.suffix != ".md":
        target_path = target_path.with_suffix(".md")
        target_page = target_path.relative_to(WIKI_DIR).as_posix()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    source_note_path = EVIDENCE_DIR / "source-notes" / f"{safe_name}.md"
    source_note_path.parent.mkdir(parents=True, exist_ok=True)

    if metadata.get("todo_scan_completed"):
        metadata["extracted_todos"] = extracted_todos

    generated_sections = dict(metadata.get("generated_sections", {}))
    if extracted_todos and _guess_page_type(target_page) == "entities":
        promoted_todos = _promote_todo_items(extracted_todos, limit=15)
        generated_sections["## Open TODOs"] = "\n".join(
            [
                f"Auto-generated from code ingest on {timestamp}. Full extraction lives in the linked source note.",
                "",
                *(_render_todo_lines(promoted_todos) or ["- None."]),
            ]
        )
    if git_repo_info and _guess_page_type(target_page) == "entities":
        source_control_lines = [
            f"Auto-generated from code ingest on {timestamp}.",
            "",
            f"- Git repository detected: yes",
            f"- Repo root: `{git_repo_info['repo_root']}`",
            f"- Active branch: `{git_repo_info['branch']}`",
        ]
        if metadata.get("git_remotes"):
            source_control_lines.extend(
                [
                    f"- Remote count: {len(metadata['git_remotes'])}",
                    "",
                    "### Remotes",
                    *_render_git_remote_lines(metadata["git_remotes"]),
                ]
            )
        elif metadata.get("git_remote_extraction_pending"):
            source_control_lines.append("- Remotes: detected but awaiting user approval for extraction")
        elif git_repo_info["remote_count"]:
            source_control_lines.append("- Remotes: detected but not extracted")
        else:
            source_control_lines.append("- Remotes: none configured")
        generated_sections["## Source Control"] = "\n".join(source_control_lines)
    if generated_sections:
        metadata["generated_sections"] = generated_sections

    synthesized_summary = (summary or content[:500]).strip()
    existing_content = target_path.read_text(encoding="utf-8") if target_path.exists() else None
    wiki_content = _build_wiki_page(
        target_page,
        source_name,
        target_path,
        source_note_path,
        synthesized_summary,
        metadata,
        existing_content=existing_content,
    )
    target_path.write_text(wiki_content, encoding="utf-8")

    # 3. Create Source Note with explicit backlinks to affected pages.
    source_note_content = _build_source_note(
        source_name=source_name,
        timestamp=timestamp,
        raw_file=raw_file,
        source_note_path=source_note_path,
        summary=synthesized_summary,
        metadata=metadata,
        affected_pages=[target_page],
    )
    source_note_path.write_text(source_note_content, encoding="utf-8")

    # 4. Update the index when a new page is created or first linked.
    created_page = existing_content is None
    index_changed = _ensure_index_entry(target_page, source_name, synthesized_summary)

    # 5. Log the action with affected artifacts.
    log_file = WIKI_DIR / "LOG.md"
    page_rel = target_path.relative_to(ROOT).as_posix()
    note_rel = source_note_path.relative_to(ROOT).as_posix()
    raw_rel = raw_file.relative_to(ROOT).as_posix()
    index_suffix = " (index updated)" if index_changed else ""
    action = "created" if created_page else "updated"
    log_entry = (
        f"\n- {timestamp}: Ingested '{source_name}' via MCP. "
        f"Wiki {action}: `{page_rel}`. Evidence: `{note_rel}`. Raw: `{raw_rel}`{index_suffix}."
    )
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_entry)

    lint_report = _scoped_lint_report([target_path, source_note_path])

    return (
        f"Successfully ingested '{source_name}'.\n"
        f"Raw: {raw_rel}\n"
        f"Note: {note_rel}\n"
        f"Wiki: {page_rel}\n"
        f"Index Updated: {'yes' if index_changed else 'no'}\n"
        f"{remote_prompt + chr(10) if remote_prompt else ''}"
        f"{lint_report}"
    )

@mcp.tool()
def memoid_log(
    entry: str,
    category: str = "session",
    context: Optional[str] = None,
    findings: Optional[List[str]] = None,
    decisions: Optional[List[str]] = None,
    follow_ups: Optional[List[str]] = None,
    affected_page_paths: Optional[List[str]] = None,
    sources: Optional[List[str]] = None,
    essential_story_update: Optional[str] = None,
) -> str:
    """
    Files durable session knowledge into a structured evidence record and LOG.md.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    normalized_pages = [_normalize_md_path(path) for path in (affected_page_paths or [])]
    page_paths = [WIKI_DIR / path for path in normalized_pages]

    session_file = EVIDENCE_DIR / "sessions" / f"{date_str}.md"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    title = f"Session Log: {date_str}"
    expected_sections = _evidence_section_template("session")
    if session_file.exists():
        existing_title, sections, order = _parse_markdown_sections(session_file.read_text(encoding="utf-8"))
        title = existing_title or title
    else:
        sections, order = {}, []
    _ensure_named_sections(sections, order, expected_sections)

    event_block = f"### {timestamp} ({category})\n{entry}"
    sections["## Events"] = _merge_block(sections.get("## Events", ""), event_block)
    if context:
        sections["## Context"] = _merge_block(sections.get("## Context", ""), context)
    if findings:
        sections["## Findings"] = _merge_list_block(sections.get("## Findings", ""), [f"- {item}" for item in findings])
    if decisions:
        sections["## Decisions"] = _merge_list_block(sections.get("## Decisions", ""), [f"- {item}" for item in decisions])
    if follow_ups:
        sections["## Follow-ups"] = _merge_list_block(sections.get("## Follow-ups", ""), [f"- {item}" for item in follow_ups])
    if normalized_pages:
        sections["## Affected Pages"] = _merge_list_block(
            sections.get("## Affected Pages", ""),
            [
                f"- [memory/wiki/{path.relative_to(WIKI_DIR).as_posix()}]({_relative_link(session_file.parent, path)})"
                for path in page_paths
            ],
        )

    lint_findings = _lint_modified_pages(page_paths) if page_paths else []
    if lint_findings:
        sections["## Findings"] = _merge_list_block(sections.get("## Findings", ""), lint_findings)

    session_file.write_text(_render_markdown_page(title, sections, order), encoding="utf-8")

    if essential_story_update:
        story_path = WIKI_DIR / "ESSENTIAL_STORY.md"
        story_content = story_path.read_text(encoding="utf-8") if story_path.exists() else "# Essential Story\n"
        if essential_story_update not in story_content:
            story_content = story_content.rstrip() + f"\n\n## Session Update\n{essential_story_update}\n"
            story_path.write_text(story_content, encoding="utf-8")

    source_lines: List[str] = []
    for source in sources or []:
        source_path = Path(source)
        resolved = source_path if source_path.is_absolute() else (ROOT / source_path)
        if resolved.exists():
            source_lines.extend(_link_lines(session_file.parent, [resolved]))
        else:
            source_lines.append(f"- {source}")
    if source_lines:
        sections["## Findings"] = _merge_list_block(sections.get("## Findings", ""), ["- Sources referenced:"] + source_lines)
        session_file.write_text(_render_markdown_page(title, sections, order), encoding="utf-8")

    log_summary = entry
    if normalized_pages:
        log_summary += f" Affected pages: {', '.join(f'`memory/wiki/{p}`' for p in normalized_pages)}."
    if lint_findings:
        log_summary += f" Lint findings: {len(lint_findings)}."
    _append_log_entry(f"- {timestamp} [{category}]: {log_summary}")

    response_lines = [
        f"Filed entry in {session_file.relative_to(ROOT).as_posix()}",
        f"Updated { (WIKI_DIR / 'LOG.md').relative_to(ROOT).as_posix() }",
        _scoped_lint_report(page_paths + [session_file]),
    ]
    if essential_story_update:
        response_lines.append(f"Updated { (WIKI_DIR / 'ESSENTIAL_STORY.md').relative_to(ROOT).as_posix() }")
    return "\n".join(response_lines)

@mcp.tool()
def memoid_edit_wiki(
    page_path: str,
    content: Optional[str] = None,
    summary: Optional[str] = None,
    title: Optional[str] = None,
    source_note_paths: Optional[List[str]] = None,
    related_page_paths: Optional[List[str]] = None,
    section_updates: Optional[Dict[str, str]] = None,
    append_mode: str = "append",
) -> str:
    """
    Creates or updates a canonical wiki page while preserving schema, sources, and index linkage.
    """
    normalized_page = _normalize_md_path(page_path)
    target_path = WIKI_DIR / normalized_page
    target_path.parent.mkdir(parents=True, exist_ok=True)

    page_type = _guess_page_type(normalized_page)
    expected_sections = _section_template(page_type)
    existing_title = ""
    if target_path.exists():
        existing_title, sections, order = _parse_markdown_sections(target_path.read_text(encoding="utf-8"))
    else:
        sections, order = {}, []
    _ensure_named_sections(sections, order, expected_sections)

    resolved_title = title or existing_title or Path(normalized_page).stem.replace("-", " ").title()
    if summary:
        sections["## Summary"] = _merge_block(sections.get("## Summary", ""), summary, mode="replace")

    body_target = {
        "entities": "## Current",
        "concepts": "## Key Ideas",
        "domains": "## Current",
        "comparisons": "## Criteria",
        "syntheses": "## Key Ideas",
    }.get(page_type, order[0] if order else "## Summary")

    if content:
        sections[body_target] = _merge_block(sections.get(body_target, ""), content, mode=append_mode)

    for heading, body in (section_updates or {}).items():
        normalized_heading = heading if heading.startswith("## ") else f"## {heading}"
        if normalized_heading not in sections:
            sections[normalized_heading] = ""
            order.append(normalized_heading)
        sections[normalized_heading] = _merge_block(sections.get(normalized_heading, ""), body, mode=append_mode)

    related_targets = [WIKI_DIR / _normalize_md_path(path) for path in (related_page_paths or [])]
    if related_targets:
        if "## Relationships" not in sections:
            sections["## Relationships"] = ""
            order.append("## Relationships")
        sections["## Relationships"] = _merge_list_block(
            sections.get("## Relationships", ""),
            _link_lines(target_path.parent, related_targets),
        )

    resolved_source_targets: List[Path] = []
    for path in source_note_paths or []:
        normalized_source = Path(path)
        if not normalized_source.suffix:
            normalized_source = normalized_source.with_suffix(".md")
        resolved = normalized_source if normalized_source.is_absolute() else (ROOT / normalized_source)
        if not resolved.exists():
            candidate = EVIDENCE_DIR / "source-notes" / normalized_source.name
            resolved = candidate
        resolved_source_targets.append(resolved)
    if resolved_source_targets:
        sections["## Sources"] = _merge_list_block(
            sections.get("## Sources", ""),
            _link_lines(target_path.parent, resolved_source_targets),
        )

    rendered = _render_markdown_page(resolved_title, sections, order)
    previous_content = target_path.read_text(encoding="utf-8") if target_path.exists() else None
    target_path.write_text(rendered, encoding="utf-8")

    summary_for_index = sections.get("## Summary", "").splitlines()[0] if sections.get("## Summary", "").strip() else resolved_title
    index_changed = _ensure_index_entry(normalized_page, resolved_title, summary_for_index)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    action = "Updated" if previous_content is not None else "Created"
    _append_log_entry(
        f"- {timestamp}: {action} wiki page `{target_path.relative_to(ROOT).as_posix()}` via MCP."
        f"{' Index updated.' if index_changed else ''}"
    )

    lint_findings = _lint_modified_pages([target_path])
    response = [
        f"{action} wiki page at {target_path.relative_to(ROOT).as_posix()}",
        f"Index Updated: {'yes' if index_changed else 'no'}",
        _scoped_lint_report([target_path]),
    ]
    return "\n".join(response)


@mcp.tool()
def memoid_audit(scope: str = "recent", page_paths: Optional[List[str]] = None, limit: int = 5) -> str:
    """
    Runs an explicit maintenance audit and writes findings to memory/evidence/audits/.
    Scope:
    - recent: pages touched in recent LOG.md entries
    - pages: explicit wiki page paths
    - full: recent pages plus orphan wiki-page detection
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    if scope == "pages":
        targets = [WIKI_DIR / _normalize_md_path(path) for path in (page_paths or [])]
    elif scope == "full":
        targets = _recent_log_paths(limit)
    else:
        targets = _recent_log_paths(limit)

    targets = [path for path in targets if path.exists()]
    findings = _lint_modified_pages(targets)
    follow_ups: List[str] = []

    if scope == "full":
        orphan_pages = _orphan_wiki_pages()
        if orphan_pages:
            findings.extend(f"- Orphan page not linked from index: `{path.relative_to(ROOT).as_posix()}`" for path in orphan_pages)
            follow_ups.extend(f"Add `{path.relative_to(ROOT).as_posix()}` to INDEX.md or link it from a canonical page." for path in orphan_pages)

    if not findings:
        follow_ups.append("No immediate maintenance action required.")
    elif not follow_ups:
        follow_ups.append("Resolve the listed findings or rerun audit after related page updates.")

    audit_file = EVIDENCE_DIR / "audits" / f"{date_str}-{scope}-audit.md"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(_build_audit_note(scope, targets, findings, follow_ups, audit_file), encoding="utf-8")

    _append_log_entry(
        f"- {timestamp} [audit]: Ran `{scope}` audit via MCP. "
        f"Report: `{audit_file.relative_to(ROOT).as_posix()}`. Findings: {len(findings)}."
    )

    response = [
        f"Audit report written to {audit_file.relative_to(ROOT).as_posix()}",
        f"Scope: {scope}",
        f"Targets: {len(targets)}",
        f"Findings: {len(findings)}",
    ]
    if findings:
        response.append("Audit Findings:")
        response.extend(findings)
    return "\n".join(response)

if __name__ == "__main__":
    mcp.run()
