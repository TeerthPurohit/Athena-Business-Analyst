"""Plain-text extraction from uploaded source documents (§ chat/document fact extraction design).

See docs/superpowers/specs/2026-08-05-ba-chat-fact-extraction-design.md. Deliberately minimal —
plain text only, no table/image/layout handling.
"""
from pathlib import Path


def extract_text(filename: str, content: bytes) -> str:
    """Extracts plain text from an uploaded file's bytes, dispatched by extension.

    Raises ValueError for unsupported extensions.
    """
    ext = Path(filename).suffix.lower()

    if ext in (".txt", ".sql", ".md", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml"):
        return content.decode("utf-8", errors="replace")

    if ext == ".docx":
        import io
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text]
        tables = [" | ".join(cell.text.strip() for cell in row.cells) for table in doc.tables for row in table.rows]
        return "\n".join(paragraphs + tables)

    if ext == ".pdf":
        import io
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    raise ValueError(f"Unsupported file type for text extraction: {ext or '(none)'}")
