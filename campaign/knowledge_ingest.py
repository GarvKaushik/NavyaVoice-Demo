"""Turns an uploaded document into short knowledge chunks (question, answer).

Accepted: .txt, .md, .csv (with question and answer columns, or any text) and .pdf.
Chunks stay inactive until an admin approves the document.
"""

import csv
import io
import os
import re
from typing import List, Tuple

MAX_CHUNK_CHARS = 500
MAX_CHUNKS = 500
ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".pdf"}


class DocumentError(ValueError):
    """The document cannot be processed; the message is safe to show the uploader."""


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise DocumentError("Could not read this file's text encoding")


def _split_long(paragraph: str) -> List[str]:
    if len(paragraph) <= MAX_CHUNK_CHARS:
        return [paragraph]
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?।])\s+", paragraph):
        while len(sentence) > MAX_CHUNK_CHARS:
            chunks.append(sentence[:MAX_CHUNK_CHARS])
            sentence = sentence[MAX_CHUNK_CHARS:]
        if current and len(current) + len(sentence) + 1 > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def _chunks_from_text(text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        paragraph = " ".join(paragraph.split()).lstrip("#-* ").strip()
        if len(paragraph) < 3:
            continue
        for chunk in _split_long(paragraph):
            out.append((chunk[:120], chunk))
    return out


def _chunks_from_csv(text: str) -> List[Tuple[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    fields = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    if "question" in fields and "answer" in fields:
        rows = [((r.get(fields["question"]) or "").strip(), (r.get(fields["answer"]) or "").strip()) for r in reader]
        return [(q, a) for q, a in rows if q and a]
    return _chunks_from_text(text)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        raise DocumentError("Could not read this PDF") from e


def parse_document(filename: str, data: bytes) -> List[Tuple[str, str]]:
    extension = os.path.splitext(filename or "")[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentError("Upload a .txt, .md, .csv or .pdf file")
    if extension == ".pdf":
        chunks = _chunks_from_text(_pdf_text(data))
    elif extension == ".csv":
        chunks = _chunks_from_csv(_decode(data))
    else:
        chunks = _chunks_from_text(_decode(data))
    if not chunks:
        raise DocumentError("No readable text found in this file")
    if len(chunks) > MAX_CHUNKS:
        raise DocumentError(f"This file is too long: {len(chunks)} sections, the limit is {MAX_CHUNKS}")
    return chunks
