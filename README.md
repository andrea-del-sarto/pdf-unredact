# pdf-unredact

`pdf-unredact` is a PDF redaction analysis and recovery tool. It can inspect PDFs where content is still present underneath visual covers and export a cleaned PDF or a side-by-side comparison while preserving the source PDF's native fonts and page content wherever possible.

The project is a substantially modified fork of **leedrake5/unredact** and is distributed under the **GNU General Public License v3.0 (GPL-3.0)**. See [`LICENSE`](LICENSE).

Release history is documented in [`CHANGELOG.md`](CHANGELOG.md).

## Web interface

Start the interface with:

```bash
python pdf_unredact.py --web
```

The app prefers `http://127.0.0.1:8765/`. If that port is already occupied, it automatically selects a free port and prints the address actually in use. Since 1.4.0 the printed startup URL contains a random one-time session token; the browser exchanges it for a local HttpOnly session cookie and then removes the token from the visible address.

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
- pressing **Export PDF** generates the file and starts the download immediately;
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

By default, the local web UI is opened in a **normal dedicated window sized/maximized to the available screen**, not in browser full-screen / kiosk mode. The title bar and normal window controls remain available. Chromium-family browsers are started maximized; Firefox-family browsers are given the detected screen dimensions as a best-effort cross-platform equivalent. `pdf-unredact` supports Chromium-family browsers (Chrome, Chromium, Edge and common derivatives such as Brave/Vivaldi/Opera) and Firefox-family browsers (Firefox plus compatible derivatives such as LibreWolf/Waterfox/Floorp/Zen when installed). A temporary profile is created for the session and the operating system default browser / normal personal profile are not used. Browser security sandboxes remain enabled.

On macOS, Safari and Safari-derived browsers are not auto-launched because Safari does not expose a supported command-line mechanism for a disposable isolated profile suitable for this launcher. If no compatible Chromium- or Firefox-family browser is available, `pdf-unredact` prints the local URL instead of silently falling back to the default browser.

Prevent automatic browser-window opening entirely:

```bash
python pdf_unredact.py --web --no-browser
```

### Local security hardening

The web interface remains bound to `127.0.0.1`, but local-only binding is not the only protection. The 1.4.0 web host also uses a random per-launch session secret, validates the `Host` header and request origin, and requires the session cookie for API access. JavaScript and CSS are served as separate same-origin resources under a strict Content Security Policy without `unsafe-inline`.

Untrusted PDF parsing for web operations is delegated to a short-lived worker process rather than being performed by the HTTP server itself. Worker execution is bounded by timeouts and concurrency limits, with best-effort operating-system CPU, output-size, and file-descriptor limits. On Linux, an address-space memory cap can be enabled explicitly with `PDF_UNREDACT_WORKER_MEMORY_MB`; it is disabled by default because native PDF libraries may reserve large virtual address ranges without consuming equivalent physical RAM. Uploads are streamed to private temporary files instead of being buffered fully in memory. Preview dimensions, page count, JSON request sizes, and upload size are also bounded.

Temporary document data is removed when a document is replaced, when the UI closes where the browser permits the cleanup request, on normal application shutdown, on SIGINT/SIGTERM, and by stale-directory cleanup on a later launch after an unclean exit. Exported temporary PDFs are removed after they are delivered to the browser.

The defaults can be adjusted for unusually large workloads with environment variables such as `PDF_UNREDACT_MAX_UPLOAD_MB`, `PDF_UNREDACT_MAX_PAGES`, `PDF_UNREDACT_MAX_WORKERS`, `PDF_UNREDACT_AUDIT_TIMEOUT`, `PDF_UNREDACT_PREVIEW_TIMEOUT`, and `PDF_UNREDACT_EXPORT_TIMEOUT`. Security limits should only be raised when needed.

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

`clean` starts from the source PDF and removes supported visual covers without re-typesetting visible text. Annotation-based covers are deleted directly. Recoverable dark rectangles embedded in the page content stream are also removed when `pdf-unredact` can safely isolate the rectangle's drawing block and rewrite that content stream without touching the underlying text. Text objects, embedded font resources, sizes, colors, positioning, images, and unrelated vector graphics are retained wherever the PDF structure permits it.

### Side-by-side

The left half reproduces the visible original page, including annotation appearances. The right half contains cleaned native PDF content. Visible text on the right is not rebuilt using fallback fonts.

---

## Redaction classification

Detected candidates now separate two independent questions:

- **Recoverable** — live PDF text still exists underneath the detected cover.
- **Removable** — the current exporter can safely remove that cover.

Removal methods are reported explicitly:

- `annotation_delete` — the cover is a supported annotation and can be deleted directly;
- `content_stream_rewrite` — the cover is an isolated dark rectangle in the page content stream and can be removed by conservatively rewriting that drawing block;
- `unsupported` — live text exists underneath, but the drawing cannot yet be isolated safely enough for automatic removal.

Non-recoverable candidates are still classified as **Probably applied** or **Uncertain**. `Probably applied` remains heuristic because a legitimate dark graphic can resemble a redaction.

The JSON report exposes `recoverable`, `removable`, and `removal_method` separately so a recoverable cover is no longer implied to be automatically removable. The old `recovery_rate` field has been replaced by `hidden_text_percentage`, which correctly means the share of all extracted text characters that lie under detected covers.

---

## Important limitations

A properly applied PDF redaction normally removes the underlying content. If the content no longer exists in the PDF, `pdf-unredact` cannot reconstruct it.

The project does not perform OCR, does not bypass encryption or passwords, and should not be treated as proof that every dark graphical region is a redaction. Content-stream rewriting is deliberately conservative. Simple `cm` transforms, safe near-opaque normal-blend ExtGState blocks, `re` rectangles, and rectangular `m/l/h` paths are supported when they can be isolated unambiguously. Complex or non-rectangular paths, clipping, images, unsupported transparency/blend states, or drawing blocks that cannot be isolated safely are reported as unsupported instead of being modified.

Editing or re-saving a PDF can affect digital signatures, certification state, incremental revisions, or evidentiary provenance. Always retain the original document unchanged.

---

## Installation

Requirements:

- Python 3.10+
- `PyMuPDF`

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
