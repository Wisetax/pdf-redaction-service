"""PDF text layout and true redaction over HTTP, built on PyMuPDF.

POST /layout?max_pages=N  body: the PDF -> {"pages": [...], "text": "...", "chars": [...]}
POST /redact              {"pdf": base64, "boxes": [...]} -> the redacted PDF (application/pdf)

Stateless: the PDF comes with each call and nothing is kept or logged. The API is described in README.md.
"""

import base64
import binascii

import orjson
import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="pdf-redaction-service", docs_url=None, redoc_url=None, openapi_url=None)

# Character boxes of two neighbouring lines overlap by about 1 pt and MuPDF removes every character that touches the
# redaction rectangle: without a margin the bottom of the line above would go too. Trimming 15 % at the top and the
# bottom still covers the body of the letters.
VERTICAL_INSET = 0.15
FONT = "helv"
MIN_FONT_SIZE = 3


def open_pdf(data: bytes) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise HTTPException(400, "unreadable PDF")
    if doc.needs_pass:
        doc.close()
        raise HTTPException(400, "password-protected PDF")
    return doc


def layout(data: bytes, max_pages: int | None = None) -> dict:
    """The text rebuilt from `page.get_text("rawdict")` (lines and pages separated by "\\n") and, for each character
    text[i], its box [page, x0, y0, x1, y1] in PDF points, or None for the line breaks added here."""
    doc = open_pdf(data)
    try:
        if max_pages is not None and doc.page_count > max_pages:
            raise HTTPException(413, f"more than {max_pages} pages")
        parts: list[str] = []
        chars: list[list | None] = []
        for pno, page in enumerate(doc):
            if pno:
                parts.append("\n")
                chars.append(None)
            for block in page.get_text("rawdict")["blocks"]:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        for ch in span["chars"]:
                            parts.append(ch["c"])
                            chars.append([pno, *(round(v, 2) for v in ch["bbox"])])
                    parts.append("\n")
                    chars.append(None)
        pages = [{"width": p.rect.width, "height": p.rect.height, "rotation": p.rotation} for p in doc]
    finally:
        doc.close()
    return {"pages": pages, "text": "".join(parts), "chars": chars}


class Box(BaseModel):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    # Texts that may be written on the rectangle, in order of preference ("[Person]", then "[P.]").
    texts: list[str] = []


class RedactRequest(BaseModel):
    pdf: str
    boxes: list[Box]


def fit(texts: list[str], rect: pymupdf.Rect) -> tuple[str | None, float]:
    """The first text that stays readable in the rectangle and its font size, else the one that fits largest, or
    (None, 0) when none fits."""
    target = rect.height * 0.85
    fitted = []
    for text in filter(None, texts):
        size = min(target, 0.95 * rect.width / pymupdf.get_text_length(text, fontname=FONT, fontsize=1))
        if size >= max(MIN_FONT_SIZE, 0.6 * target):
            return text, size
        fitted.append((text, size))
    text, size = max(fitted, key=lambda f: f[1], default=(None, 0))
    return (text, size) if size >= MIN_FONT_SIZE else (None, 0)


def redact(data: bytes, boxes: list[Box]) -> bytes:
    """The text under each rectangle is removed from the file (not just painted over) and a black rectangle is drawn
    in its place, with one of the box's texts on it when it fits. Metadata is cleared."""
    doc = open_pdf(data)
    try:
        labels = []
        for b in boxes:
            if not 0 <= b.page < doc.page_count:
                raise HTTPException(400, f"page {b.page} does not exist")
            inset = VERTICAL_INSET * (b.y1 - b.y0)
            rect = pymupdf.Rect(b.x0, b.y0 + inset, b.x1, b.y1 - inset)
            doc[b.page].add_redact_annot(rect, fill=(0, 0, 0))
            # Written after the redaction as real (extractable) text, white on black, because MuPDF does not write
            # an annotation's text under ~7 pt of rectangle height, i.e. most table rows.
            text, size = fit(b.texts, rect)
            if text:
                labels.append((b.page, rect, text, size))
        for page in doc:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
        for pno, rect, text, size in labels:
            width = pymupdf.get_text_length(text, fontname=FONT, fontsize=size)
            # Centred; baseline at mid-height plus a third of the size (cap height).
            origin = pymupdf.Point((rect.x0 + rect.x1 - width) / 2, (rect.y0 + rect.y1) / 2 + size * 0.35)
            doc[pno].insert_text(origin, text, fontname=FONT, fontsize=size, color=(1, 1, 1))
        doc.set_metadata({})
        doc.del_xml_metadata()
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/layout")
async def layout_route(request: Request, max_pages: int | None = None):
    result = await run_in_threadpool(layout, await request.body(), max_pages)
    return Response(orjson.dumps(result), media_type="application/json")


@app.post("/redact")
def redact_route(request: RedactRequest):
    try:
        data = base64.b64decode(request.pdf, validate=True)
    except binascii.Error:
        raise HTTPException(400, "pdf must be base64-encoded")
    return Response(redact(data, request.boxes), media_type="application/pdf")
