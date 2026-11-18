# pdf-redaction-service

A small stateless HTTP service that reads the text layout of a PDF and applies true redactions to it, built on [PyMuPDF](https://github.com/pymupdf/PyMuPDF).

Nothing is stored between calls and nothing the service receives is logged.

## Licence

GNU Affero General Public License v3.0, see `LICENSE`. PyMuPDF is itself distributed under the AGPL-3.0.

## Run

```
docker build -t pdf-redaction-service .
docker run -p 8633:8633 pdf-redaction-service
```

## Test

```
pip install -r requirements.txt pytest httpx
pytest
```

## API

Coordinates are PDF points in the page's unrotated frame, origin at the top-left of the CropBox. Text offsets are Unicode code points.

### `POST /layout?max_pages=N`

Body: the PDF (`application/pdf`). `max_pages` is optional; above it the answer is 413.

```json
{
  "pages": [{"width": 595.0, "height": 842.0, "rotation": 0}],
  "text": "First line\nSecond line\n",
  "chars": [[0, 72.0, 89.4, 78.1, 104.5], null]
}
```

- `pages`: the size of each page as displayed (rotation applied) and its rotation in degrees, clockwise.
- `text`: the text of the document, lines and pages separated by `\n`.
- `chars`: one entry per character of `text`, `[page, x0, y0, x1, y1]`, or `null` for the line breaks the service adds.

400 when the PDF is unreadable or password-protected.

### `POST /redact`

```json
{
  "pdf": "<base64>",
  "boxes": [{"page": 0, "x0": 72.0, "y0": 89.4, "x1": 140.2, "y1": 104.5, "texts": ["[Person]", "[P.]"]}]
}
```

Answer: the redacted PDF (`application/pdf`). The text under each box is removed from the file, not painted over, and a black rectangle is drawn in its place. Images are left untouched. `texts` is optional: the first text that stays readable in the rectangle is written on it in white, else the one that fits largest, else nothing. Document metadata is cleared.

Each box is trimmed by 15 % at the top and at the bottom before redaction, so that a box taken from `/layout` does not remove the characters of the neighbouring lines.

400 when the PDF is unreadable, when `pdf` is not base64, or when a box names a page that does not exist.

### `GET /health`

`{"ok": true}`.
