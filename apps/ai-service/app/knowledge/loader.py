import logging
import re
from pathlib import Path

from app.knowledge.models import PolicyChunk

logger = logging.getLogger(__name__)

DOCUMENT_ID_PATTERN = re.compile(r"<!--\s*document_id:\s*([a-z0-9-]+)\s*-->", re.IGNORECASE)
HEADING_PATTERN = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


def load_policy_directory(directory: Path) -> list[PolicyChunk]:
    if not directory.is_dir():
        logger.warning("Knowledge directory not found: %s", directory)
        return []
    chunks: list[PolicyChunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(load_policy(path))
    return chunks


def load_policy(path: Path) -> list[PolicyChunk]:
    content = path.read_text(encoding="utf-8")
    document_id_match = DOCUMENT_ID_PATTERN.search(content)
    if document_id_match is None:
        raise ValueError(f"Policy {path.name} is missing document_id")

    document_id = document_id_match.group(1)
    title, sections = _parse_markdown_sections(content)
    if not title:
        raise ValueError(f"Policy {path.name} is missing an H1 title")
    return _build_policy_chunks(document_id, title, sections)


def chunk_policy_markdown(document_id: str, title: str, content: str) -> list[PolicyChunk]:
    """Apply the established H2/H3 and 500/50 chunk rules to supplied Markdown."""
    _, sections = _parse_markdown_sections(content)
    return _build_policy_chunks(document_id, title, sections)


def _parse_markdown_sections(content: str) -> tuple[str, list[tuple[str, str]]]:
    title = ""
    current_section = ""
    section_lines: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush_section() -> None:
        if current_section and section_lines:
            text = "\n".join(section_lines).strip()
            if text:
                sections.append((current_section, text))

    for line in content.splitlines():
        heading_match = HEADING_PATTERN.match(line)
        if heading_match is None:
            if current_section:
                section_lines.append(line)
            continue

        level = len(heading_match.group(1))
        heading = heading_match.group(2).strip()
        if level == 1:
            title = heading
            continue
        flush_section()
        current_section = heading
        section_lines = []

    flush_section()
    return title, sections


def _build_policy_chunks(
    document_id: str,
    title: str,
    sections: list[tuple[str, str]],
) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    for section_index, (section, text) in enumerate(sections):
        for chunk_index, chunk_text in enumerate(_split_text(text)):
            chunks.append(
                PolicyChunk(
                    chunk_id=f"{document_id}:{section_index}:{chunk_index}",
                    document_id=document_id,
                    title=title,
                    section=section,
                    text=chunk_text,
                )
            )
    return chunks


def _split_text(text: str, max_chars: int = 500, overlap: int = 50) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            punctuation = max(text.rfind(mark, start + 200, end) for mark in "。！？；")
            if punctuation > start:
                end = punctuation + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks
