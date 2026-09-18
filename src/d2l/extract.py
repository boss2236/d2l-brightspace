"""Plain text out of course files, so search and the AI can read lecture notes. Unknown types return ''."""
import html
import logging
import re
import zipfile
from pathlib import Path

logging.getLogger("pypdf").setLevel(logging.ERROR)     # font-encoding chatter on every maths PDF

# what gets downloaded at all; videos and archives are skipped
TEXT_TYPES = {"pdf", "docx", "pptx", "html", "htm", "txt", "md", "csv"}
MAX_BYTES = 40 * 1024 * 1024


def _tidy(t: str) -> str:
    """pypdf writes some PDFs one word per line (with ' ' lines between); join those pages back into prose."""
    t = re.sub(r"(\s?\.){5,}", " … ", t)                   # table-of-contents dot leaders
    lines = [l.strip() for l in t.split("\n")]
    words = [l for l in lines if l]
    if len(words) > 8 and sum(len(l.split()) for l in words) / len(words) < 1.3:
        return " ".join(words)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _html_text(raw: str) -> str:
    raw = raw.replace("\r", "").lstrip("\ufeff")
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw)).replace("\xa0", " ").lstrip("\ufeff")
    return re.sub(r"[ \t]*\n\s*", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def text_of(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext not in ("docx", "pptx") and zipfile.is_zipfile(path):
        # Brightspace serves HTML topics as a zip of the page plus its assets
        with zipfile.ZipFile(path) as z:
            pages = [n for n in z.namelist() if n.lower().endswith((".html", ".htm", ".txt"))]
            return "\n\n".join(_html_text(z.read(n).decode("utf-8", "ignore")) for n in pages).strip()
    try:
        if ext == "pdf":
            from pypdf import PdfReader
            pages = PdfReader(path).pages
            text = "\n\n".join(f"[page {i}]\n{_tidy(p.extract_text() or '')}" for i, p in enumerate(pages, 1)).strip()
            words = len(re.sub(r"\[page \d+\]", "", text).split())
            if words < 25 * len(pages):
                text = ("[mostly images or handwriting: only a little text could be extracted — open the file to read it]\n\n"
                        + text)
            return text
        if ext == "docx":
            from docx import Document
            return "\n".join(p.text for p in Document(path).paragraphs if p.text.strip())
        if ext == "pptx":
            from pptx import Presentation
            slides = []
            for i, s in enumerate(Presentation(path).slides, 1):
                bits = [sh.text_frame.text for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
                slides.append(f"[slide {i}]\n" + "\n".join(bits))
            return "\n\n".join(slides)
        if ext in ("html", "htm"):
            return _html_text(path.read_text(errors="ignore"))
        if ext in ("txt", "md", "csv"):
            return path.read_text(errors="ignore")
    except Exception as e:                      # a broken file shouldn't stop the sync
        return f"[could not extract text: {e.__class__.__name__}]"
    return ""


def reindex(db, root: Path) -> int:
    """Re-extract text from files already downloaded (after improving the extractor); no network."""
    n = 0
    for r in db.execute("SELECT id, file_path FROM content WHERE file_status = 'ok'").fetchall():
        db.execute("UPDATE content SET text = ? WHERE id = ?", (text_of(root / r[1]), r[0]))
        n += 1
    return n
