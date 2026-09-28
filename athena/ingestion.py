"""Deterministic transformation from uploaded text bytes to source-span drafts."""

import hashlib
from dataclasses import dataclass

from athena.storage import ObjectStore


@dataclass(frozen=True)
class SourceSpanDraft:
    ordinal: int
    start_char: int
    end_char: int
    raw_text: str
    normalized_text_hash: str


@dataclass(frozen=True)
class IngestedSourceDraft:
    org_id: str
    project_id: str
    content_hash: str
    storage_key: str
    storage_ref: str
    spans: tuple[SourceSpanDraft, ...]


def segment_text(text: str) -> tuple[SourceSpanDraft, ...]:
    """Split non-empty source lines while preserving offsets into the original text."""
    spans: list[SourceSpanDraft] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        normalized = raw.strip()
        if normalized:
            start_char = cursor + len(raw) - len(raw.lstrip())
            spans.append(
                SourceSpanDraft(
                    ordinal=len(spans),
                    start_char=start_char,
                    end_char=start_char + len(normalized),
                    raw_text=normalized,
                    normalized_text_hash=hashlib.sha256(normalized.encode()).hexdigest(),
                )
            )
        cursor += len(line)
    return tuple(spans)


def ingest_text_source(
    *,
    org_id: str,
    project_id: str,
    filename: str,
    content: bytes,
    media_type: str,
    store: ObjectStore,
) -> IngestedSourceDraft:
    """Validate text before making its immutable content-addressed storage durable."""
    if not org_id or not project_id:
        raise ValueError("org_id and project_id are required.")
    if not filename:
        raise ValueError("filename is required.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Source content must be valid UTF-8 text.") from error

    content_hash = hashlib.sha256(content).hexdigest()
    storage_key = f"sha256/{content_hash}"
    storage_ref = store.put_if_absent(storage_key, content, media_type)
    return IngestedSourceDraft(
        org_id=org_id,
        project_id=project_id,
        content_hash=content_hash,
        storage_key=storage_key,
        storage_ref=storage_ref,
        spans=segment_text(text),
    )
