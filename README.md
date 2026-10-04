# pdf-unredact

**Analyze PDF redactions, detect recoverable hidden text, and export cleaned documents without re-typesetting the original content.**

`pdf-unredact` is a PDF redaction analysis and recovery tool designed for documents where text or page content may still exist underneath visual covers. It can inspect suspicious redactions, classify what is recoverable and removable, and export either a cleaned PDF or an original/cleaned side-by-side comparison.

Wherever possible, the project preserves the source PDF's native fonts, text objects, positioning, colors, images, and vector content instead of rebuilding the page.

> **Important:** properly applied PDF redactions remove the underlying content. If the original content is no longer present in the file, `pdf-unredact` cannot reconstruct it.

---

## Highlights

- **Web interface** with English and Italian localization
- **Clean PDF export** that preserves native PDF content where possible
- **Side-by-side comparison** between original and cleaned pages
- **Automatic redaction analysis** with detailed statistics
- **JSON audit reports** for programmatic inspection
- **Selective annotation removal** or full annotation removal
- **Page preview and findings table**
- **Recoverable vs removable classification**
- **Conservative content-stream rewriting** for supported dark rectangles
- **Local-only web server** with multiple security hardening layers
- **CLI support** for scripted workflows

---

## Quick start

### Launch the web interface

```bash
python pdf_unredact.py --web
```

The application prefers:

```text
http://127.0.0.1:8765/
```

If that port is unavailable, a free port is selected automatically and the actual address is printed in the terminal.

Since version **1.4.0**, the startup URL includes a random one-time session token. The browser exchanges it for a local HttpOnly session cookie and then removes the token from the visible address.

### Use a custom port

```bash
python pdf_unredact.py --web --port 9000
```

### Start without opening a browser

```bash
python pdf_unredact.py --web --no-browser
```

---

## Web interface

The web interface exposes the main document-processing features available from the CLI while providing visual inspection and export tools.

You can:

- choose an input PDF;
- select `clean` or `side_by_side` output;
- remove only redaction-like annotations or remove all annotations;
- choose the output filename;
- export the generated PDF directly;
- run automatic redaction analysis;
- inspect analysis statistics;
- download a detailed JSON report;
- preview original and cleaned pages, with an optional full-screen comparison view;
- inspect page-by-page finding details in a section collapsed by default;
- manually refresh the analysis.

The CLI-only `--port` and `--no-browser` options control how the local web host is started; they are not document-processing settings.

### Browser behavior

By default, the local interface opens in a **normal dedicated browser window**, sized or maximized to the available screen rather than using kiosk or browser full-screen mode.

Supported browser families include:

- Chromium-based browsers such as Chrome, Chromium, Edge, Brave, Vivaldi, and Opera;
- Firefox-based browsers such as Firefox, LibreWolf, Waterfox, Floorp, and Zen.

A temporary browser profile is created for the session, so the user's normal default browser profile is not reused. Browser security sandboxes remain enabled.

On macOS, Safari and Safari-derived browsers are not auto-launched because Safari does not expose a suitable command-line mechanism for creating the disposable isolated profile required by the launcher. If no compatible Chromium- or Firefox-family browser is available, the application prints the local URL instead.

---

## Languages

The interface and backend messages are available in:

- **English** (`en`)
- **Italian** (`it`)

Use the language button in the top-right corner to switch between `IT` and `EN`.

On first launch, Italian is selected when the browser or system language starts with `it`; otherwise English is used. The selected language is remembered locally.

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

- `frontend` contains labels, buttons, tooltips, status messages, statistics labels, finding names, preview text, export messages, and other interface strings.
- `backend` contains API/server errors and backend messages.

The frontend loads translations through the local application API, while the backend uses the same JSON files through `i18n.py`. This keeps translations centralized instead of duplicating them across Python and JavaScript.

`i18n.py` also verifies that the English and Italian files expose the same translation keys. Missing or extra keys are detected at startup. English is used as the fallback language for unsupported locales or unavailable keys.

Changing language updates the interface without reloading the application, including dynamically generated states and localized API errors.

---

## Local security hardening

The web interface is bound to `127.0.0.1`, but local-only binding is only one part of the security model.

Since version **1.4.0**, the web host also uses:

- a random per-launch session secret;
- `Host` header validation;
- request-origin validation;
- a required session cookie for API access;
- same-origin JavaScript and CSS;
- a strict Content Security Policy without `unsafe-inline`.

### Isolated PDF processing

Untrusted PDF parsing is delegated to the same short-lived worker process for both web operations and CLI processing instead of being performed directly by the host process. Since **1.4.4**, `EngineClient` also uses opaque input/output capabilities, stages inputs into immutable private storage, and commits validated exports atomically. A future Tauri or Flutter frontend therefore does not receive worker paths or authorize its own filesystem roots.

Worker execution is bounded by:

- timeouts;
- concurrency limits;
- a parent-side resident-memory (RSS) monitor;
- best-effort CPU limits;
- output-size limits;
- file-descriptor limits.

The parent process monitors the worker's resident memory on supported Windows, macOS, and Linux systems. The default limit is 1536 MB and can be adjusted or disabled with:

```text
PDF_UNREDACT_WORKER_MAX_RSS_MB
```

A value of `0` disables the RSS cap.

On Linux, an additional address-space memory cap can be enabled with:

```text
PDF_UNREDACT_WORKER_MEMORY_MB
```

The address-space cap is disabled by default because native PDF libraries may reserve large virtual address ranges without consuming the same amount of physical RAM.

Uploads are streamed to private temporary files rather than buffered entirely in memory. Preview dimensions, page count, JSON request size, input size, output size, active job count, total workspace use, and minimum free-disk headroom are also bounded.

Since **1.4.1**, exported PDFs are sanitized by default to remove active or attached content such as PDF JavaScript, active links/actions, and embedded files. Sanitization can be disabled explicitly. In **1.4.4**, fail-closed verification also covers XFA, JavaScript name trees, RichMedia/Screen/file-attachment annotations, and additional executable or external action types. Hidden text and redaction content remain preserved so recoverable text is not destroyed by the security pass.

Temporary work directories include an application marker so stale-cleanup only removes directories that were actually created by `pdf-unredact`. Temporary document data is removed when:

- a document is replaced;
- the UI closes and the browser permits the cleanup request;
- the application shuts down normally;
- the process receives `SIGINT` or `SIGTERM`;
- stale temporary directories are detected on a later launch after an unclean exit.

Temporary exported PDFs are removed after delivery to the browser.

For unusually large workloads, limits can be adjusted with environment variables such as:

```text
PDF_UNREDACT_MAX_UPLOAD_MB
PDF_UNREDACT_MAX_OUTPUT_MB
PDF_UNREDACT_MAX_WORKSPACE_MB
PDF_UNREDACT_MIN_FREE_MB
PDF_UNREDACT_MAX_ACTIVE_JOBS
PDF_UNREDACT_MAX_PAGES
PDF_UNREDACT_MAX_WORKERS
PDF_UNREDACT_WORKER_MAX_RSS_MB
PDF_UNREDACT_AUDIT_TIMEOUT
PDF_UNREDACT_PREVIEW_TIMEOUT
PDF_UNREDACT_EXPORT_TIMEOUT
```

Security limits should only be raised when necessary.

---

## CLI

The command-line interface remains fully available for scripted or headless workflows. Since version **1.4.4**, CLI and web operations use engine-owned immutable input staging, opaque input/output/analysis handles, and atomic output commits in addition to the shared `EngineClient` validation layer and short-lived PDF worker.

Release builds use `constraints-release.txt` to pin the tested PyMuPDF runtime and Hatchling build backend.

### Command summary

| Command | Purpose |
|---|---|
| `python pdf_unredact.py --web` | Start the graphical web interface |
| `python pdf_unredact.py --web --port 9000` | Start the web interface on a preferred port |
| `python pdf_unredact.py --web --no-browser` | Start the web interface without opening a browser |
| `python pdf_unredact.py --version` | Show the installed version |
| `python pdf_unredact.py document.pdf --mode clean` | Export a cleaned PDF |
| `python pdf_unredact.py document.pdf --mode side_by_side` | Export an original/cleaned comparison |
| `python pdf_unredact.py document.pdf --mode clean --stats` | Export a cleaned PDF and print statistics |
| `python pdf_unredact.py document.pdf --mode clean --stats-json audit.json` | Export a cleaned PDF and save a JSON report |
| `python pdf_unredact.py document.pdf --mode clean --remove all-annotations` | Remove every annotation during clean export |
| `python pdf_unredact.py document.pdf --no-sanitize-active-content` | Export while preserving PDF active content (sanitization is on by default) |
| `python pdf_unredact.py document.pdf --mode clean -o output.pdf` | Choose an explicit output filename |

### Basic usage

```bash
python pdf_unredact.py document.pdf --mode clean
python pdf_unredact.py document.pdf --mode side_by_side
```

### Print or save analysis data

```bash
python pdf_unredact.py document.pdf --mode clean --stats
python pdf_unredact.py document.pdf --mode clean --stats --stats-json audit.json
```

### Remove all annotations

By default, only redaction-like annotations are removed.

To deliberately remove every annotation:

```bash
python pdf_unredact.py document.pdf --mode clean --remove all-annotations
```

---

## Output behavior

### Clean PDF

`clean` starts from the source PDF and removes supported visual covers without re-typesetting visible text.

Annotation-based covers are deleted directly. Recoverable dark rectangles embedded in the page content stream can also be removed when `pdf-unredact` can safely isolate the corresponding drawing block and rewrite that part of the content stream without touching the underlying text.

Where the PDF structure permits it, the exporter preserves:

- text objects;
- embedded font resources;
- font sizes and colors;
- text positioning;
- images;
- unrelated vector graphics.

### Side-by-side

The left half reproduces the visible original page, including annotation appearances.

The right half contains cleaned native PDF content. Visible text on the right is not rebuilt using fallback fonts.

---

## Redaction classification

Detected candidates separate two different questions:

- **Recoverable** — does live PDF text still exist underneath the detected cover?
- **Removable** — can the current exporter safely remove that cover?

### Removal methods

| Method | Meaning |
|---|---|
| `annotation_delete` | The cover is a supported annotation and can be deleted directly |
| `content_stream_rewrite` | The cover is an isolated dark rectangle that can be safely removed by rewriting its drawing block |
| `unsupported` | Live text exists underneath, but the drawing cannot yet be isolated safely enough for automatic removal |

Non-recoverable candidates are still classified as **Probably applied** or **Uncertain**.

`Probably applied` is heuristic: a legitimate dark graphic can still resemble a redaction.

The JSON report exposes `recoverable`, `removable`, and `removal_method` separately so that a recoverable cover is not automatically implied to be removable.

The former `recovery_rate` field has been replaced by `hidden_text_percentage`, meaning the share of all extracted text characters that lie underneath detected covers.

---

## Important limitations

A properly applied PDF redaction normally removes the underlying content. If the content no longer exists in the PDF, `pdf-unredact` cannot reconstruct it.

The project:

- does **not** perform OCR;
- does **not** bypass PDF encryption or passwords;
- does **not** treat every dark graphical region as proof of redaction.

Content-stream rewriting is intentionally conservative.

Supported cases include simple `cm` transforms, safe near-opaque normal-blend ExtGState blocks, `re` rectangles, and rectangular `m/l/h` paths when they can be isolated unambiguously.

Complex or non-rectangular paths, clipping, images, unsupported transparency or blend states, and drawing blocks that cannot be safely isolated are reported as unsupported instead of being modified.

> Editing or re-saving a PDF can affect digital signatures, certification state, incremental revisions, or evidentiary provenance. Always retain the original document unchanged.

---

## Installation

### Requirements

- Python **3.10+**
- `PyMuPDF`

### With `uv`

```bash
uv sync
uv run python pdf_unredact.py --web
```

Alternatively, install the dependencies using your preferred Python environment and run `pdf_unredact.py` directly.

---

## License and attribution

`pdf-unredact` is distributed under the **GNU General Public License v3.0 (GPL-3.0)**. The full license text is included in [`LICENSE`](LICENSE).

This project is a substantially modified fork of **leedrake5/unredact**:

https://github.com/leedrake5/unredact

Current project repository:

https://github.com/andrea-del-sarto/pdf-unredact

The fork preserves attribution to the GPLv3-licensed upstream project while introducing, among other changes:

- native PDF-content preservation;
- `clean` export;
- selective redaction removal;
- expanded redaction analysis and classification;
- JSON reporting;
- a rewritten side-by-side mode;
- the `pymupdf` import name;
- a redesigned bilingual interface.

Parts of this fork were developed with AI-assisted coding tools ("vibe coding") as part of the implementation workflow. Changes were reviewed, tested, and adapted before inclusion.

---

## Release history

See [`CHANGELOG.md`](CHANGELOG.md) for release notes and version history.
