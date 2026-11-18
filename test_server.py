"""Layout and true redaction, through the HTTP routes, on a PDF built in the test."""

import base64

import pymupdf
import pytest
from fastapi.testclient import TestClient

from server import app

client = TestClient(app)
LINES = ["Agreement between Jane DOE and the company", "NORTHWIND WORKSHOPS, represented by its manager."]


def make_pdf(lines=LINES, pages=1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + 14 * i), line, fontsize=11)
    doc.set_metadata({"author": "Jane DOE", "title": "DOE agreement"})
    data = doc.tobytes()
    doc.close()
    return data


def layout(data: bytes, **params) -> dict:
    response = client.post("/layout", content=data, params=params)
    assert response.status_code == 200
    return response.json()


def box(chars: list, start: int, end: int, **more) -> dict:
    """One rectangle around text[start:end], which sits on a single line in these tests."""
    found = [c for c in chars[start:end] if c]
    return {"page": found[0][0], "x0": min(c[1] for c in found), "y0": min(c[2] for c in found),
            "x1": max(c[3] for c in found), "y1": max(c[4] for c in found), **more}


def redact(data: bytes, boxes: list[dict]) -> pymupdf.Document:
    response = client.post("/redact", json={"pdf": base64.b64encode(data).decode(), "boxes": boxes})
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    return pymupdf.open(stream=response.content, filetype="pdf")


def test_layout_gives_one_box_per_character():
    got = layout(make_pdf(pages=2))
    text, chars = got["text"], got["chars"]
    assert len(got["pages"]) == 2 and got["pages"][0]["rotation"] == 0
    assert len(text) == len(chars)
    assert text.split("\n")[:2] == LINES
    assert all((c is None) == (ch == "\n") for ch, c in zip(text, chars))
    assert chars[text.rindex("NORTHWIND")][0] == 1  # second page


def test_layout_refuses_too_many_pages_and_unreadable_files():
    assert client.post("/layout", content=make_pdf(pages=2), params={"max_pages": 1}).status_code == 413
    assert client.post("/layout", content=b"not a pdf").status_code == 400


def test_redact_removes_the_text_keeps_the_neighbours_and_writes_the_first_text_that_fits():
    data = make_pdf()
    got = layout(data)
    start = got["text"].index("Jane DOE")
    red = redact(data, [box(got["chars"], start, start + len("Jane DOE"), texts=["[Person]", "[P.]"])])
    content = red[0].get_text()
    assert "DOE" not in content and "Jane" not in content
    assert "[Person]" in content
    # Words on the same line and on the line below survive.
    assert "Agreement between" in content and "NORTHWIND WORKSHOPS" in content
    assert not any(red.metadata.get(k) for k in ("author", "title"))


def test_redact_falls_back_to_the_text_that_fits_a_narrow_rectangle():
    data = make_pdf()
    got = layout(data)
    start = got["text"].index("and")
    content = redact(data, [box(got["chars"], start, start + 3, texts=["[Date of birth]", "[X]"])])[0].get_text()
    assert "[X]" in content and "[Date of birth]" not in content


def test_redact_without_text_draws_black_only():
    data = make_pdf()
    got = layout(data)
    start = got["text"].index("NORTHWIND")
    content = redact(data, [box(got["chars"], start, start + 9)])[0].get_text()
    assert "NORTHWIND" not in content and "[" not in content


@pytest.mark.parametrize("payload", [
    {"pdf": "%%%", "boxes": []},
    {"pdf": base64.b64encode(make_pdf()).decode(), "boxes": [{"page": 3, "x0": 0, "y0": 0, "x1": 10, "y1": 10}]},
    {"pdf": base64.b64encode(b"not a pdf").decode(), "boxes": []},
])
def test_redact_rejects_bad_input(payload):
    assert client.post("/redact", json=payload).status_code == 400
