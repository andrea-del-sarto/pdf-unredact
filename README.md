# pdf-unredact

**Version:** `1.1.1`  
**Repository:** https://github.com/andrea-del-sarto/pdf-unredact

`pdf-unredact` is a PDF redaction analysis and recovery tool. It can inspect PDFs where content is still present underneath visual covers and export a cleaned PDF or a side-by-side comparison while preserving the source PDF's native fonts and page content wherever possible.

The project is a substantially modified fork of **leedrake5/unredact** and is distributed under the **GNU General Public License v3.0 (GPL-3.0)**. See [`LICENSE`](LICENSE).

Release history is documented in [`CHANGELOG.md`](CHANGELOG.md).

## Web interface

Start the interface with:

```bash
python pdf_unredact.py --web
```

The app prefers `http://127.0.0.1:8765/`. If that port is already occupied, it automatically selects a free port and prints the address actually in use.

The interface is designed as the basis for a desktop application on Windows, macOS, and Linux while keeping the same PDF-processing engine and workflow.

### Languages

The interface and backend messages are available in:

- **English** (`en`)
- **Italian** (`it`)

Use the language button in the top-right corner to switch between `IT` and `EN`. On first launch, Italian is selected when the system/browser language starts with `it`; otherwise English is used. The selected language is remembered locally.

Translations are stored outside the Python and JavaScript application code:

```text
locales/
├── en.json
└── it.json
```

Each locale file contains two catalogs:

```json
{
  "frontend": {},
  "backend": {}
}
```

- `frontend` contains labels, buttons, tooltips, status messages, statistics labels, findings/status names, preview text, export messages, and other interface strings.
- `backend` contains API/server errors and backend messages.

The frontend loads its catalog from the local application API (`/api/i18n/en` or `/api/i18n/it`). The backend reads the same JSON files through `i18n.py`, so translations are maintained in one place rather than duplicated in Python and JavaScript.

`i18n.py` also validates that the English and Italian files expose the same translation keys. Missing or extra keys are therefore detected at startup. English is used as the fallback language for unsupported locales or unavailable keys.

Changing language updates the interface without reloading the application, including dynamically generated states and localized API errors.

### Processing options

The web interface exposes all document-processing options currently available from the CLI:

- choose the input PDF;
- `clean` or `side_by_side` output;
- remove only detected redaction-like annotations or remove all annotations;
- choose the output filename (web equivalent of `-o/--output`);
- automatic redaction analysis and statistics (`--stats` equivalent);
- downloadable detailed JSON report (`--stats-json` equivalent);
- original vs cleaned page preview;
- page-by-page findings table;
- manual analysis refresh.

The CLI-only `--port` and `--no-browser` flags control how the web host is started and are not document-processing settings.

Use another port:

```bash
python pdf_unredact.py --web --port 9000
```

Prevent automatic browser opening:

```bash
python pdf_unredact.py --web --no-browser
```

---

## CLI

### Command summary

| Command | Purpose |
|---|---|
| `python pdf_unredact.py --web` | Start the graphical web interface |
| `python pdf_unredact.py --web --port 9000` | Start the web interface on a preferred port |
| `python pdf_unredact.py --web --no-browser` | Start the web interface without opening the browser automatically |
| `python pdf_unredact.py --version` | Show the installed `pdf-unredact` version |
| `python pdf_unredact.py document.pdf --mode clean` | Export a cleaned PDF |
| `python pdf_unredact.py document.pdf --mode side_by_side` | Export an original/cleaned side-by-side comparison |
| `python pdf_unredact.py document.pdf --mode clean --stats` | Export a cleaned PDF and print redaction statistics |
| `python pdf_unredact.py document.pdf --mode clean --stats-json audit.json` | Export a cleaned PDF and save the detailed JSON report |
| `python pdf_unredact.py document.pdf --mode clean --remove all-annotations` | Export a cleaned PDF while removing every annotation |
| `python pdf_unredact.py document.pdf --mode clean -o output.pdf` | Choose an explicit output filename |

The command-line interface remains available:

```bash
python pdf_unredact.py document.pdf --mode clean
python pdf_unredact.py document.pdf --mode side_by_side
```

Print or save analysis information:

```bash
python pdf_unredact.py document.pdf --mode clean --stats
python pdf_unredact.py document.pdf --mode clean --stats --stats-json audit.json
```

By default only redaction-like annotations are removed. To deliberately remove all annotations:

```bash
python pdf_unredact.py document.pdf --mode clean --remove all-annotations
```

---

## Output behavior

### Clean PDF

`clean` starts from the source PDF and removes selected annotation-based covers without re-typesetting visible text. Text objects, embedded font resources, sizes, colors, positioning, images, and vector graphics are retained wherever the PDF structure permits it.

### Side-by-side

The left half reproduces the visible original page, including annotation appearances. The right half contains cleaned native PDF content. Visible text on the right is not rebuilt using fallback fonts.

---

## Redaction classification

Detected candidates are classified as:

- **Recoverable** — live PDF text overlaps the detected cover.
- **Probably applied** — a dark redaction-like rectangle is present but no live text is found underneath. This is heuristic and can include legitimate graphics.
- **Uncertain** — a redaction-like annotation exists but recoverable content cannot be established confidently.

The tool recognizes dedicated PDF Redact annotations and some improvised covers such as opaque black Highlight or Square annotations. It can also inspect dark rectangles in the page drawing stream for analysis purposes.

---

## Important limitations

A properly applied PDF redaction normally removes the underlying content. If the content no longer exists in the PDF, `pdf-unredact` cannot reconstruct it.

The project does not perform OCR, does not bypass encryption or passwords, and should not be treated as proof that every dark graphical region is a redaction.

Editing or re-saving a PDF can affect digital signatures, certification state, incremental revisions, or evidentiary provenance. Always retain the original document unchanged.

---

## Installation

Requirements:

- Python 3.10+
- `PyMuPDF`
- `pdfplumber`

With `uv`:

```bash
uv sync
uv run python pdf_unredact.py --web
```

Or install the dependencies with your preferred Python environment and run `pdf_unredact.py` directly.

---

## Licence

`pdf-unredact` is licensed under the **GNU General Public License v3.0**. The full licence text is included in [`LICENSE`](LICENSE).

This project is a substantially modified fork of **leedrake5/unredact**:

https://github.com/leedrake5/unredact

The current project repository is:

https://github.com/andrea-del-sarto/pdf-unredact

The fork preserves attribution to the GPLv3-licensed upstream project while introducing native PDF-content preservation, `clean` export, selective redaction removal, expanded redaction analysis/classification, JSON reporting, a rewritten side-by-side mode, the `pymupdf` import name, and the redesigned bilingual interface.

Parts of this fork were developed with AI-assisted coding tools ("vibe coding") as part of the implementation workflow. Changes were reviewed, tested, and adapted before inclusion.
