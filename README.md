# pdf-unredact

**Version:** `1.0.0`  
**Status:** first official release of the `pdf-unredact` fork

`pdf-unredact` is a PDF redaction analysis and recovery tool. It can inspect PDFs where content is still present underneath visual covers and export a cleaned PDF or a side-by-side comparison while preserving the source PDF's native fonts and page content wherever possible.

Project repository:

https://github.com/andrea-del-sarto/pdf-unredact

This project is a substantially modified fork of **leedrake5/unredact**:

https://github.com/leedrake5/unredact

The upstream project and this fork are distributed under the **GNU General Public License v3.0 (GPL-3.0)**. See [`LICENSE`](LICENSE).

Release history is documented in [`CHANGELOG.md`](CHANGELOG.md).

## Version 1.0.0

`1.0.0` is the **first official version of this fork** and the baseline release of the `pdf-unredact` project.

It includes:

- the rewritten native-PDF cleaning engine;
- `clean` and `side_by_side` output modes;
- selective removal of redaction-like annotations;
- recoverable / probably applied / uncertain classification;
- JSON analysis reports;
- the graphical web interface intended as the basis for the desktop application;
- English and Italian interface localization;
- System / Light / Dark themes with operating-system preference detection;
- the application information dialog with app name, version, and project repository;
- automatic fallback to a free port when the preferred web port is already occupied;
- preservation of original PDF font resources and layout wherever the source PDF structure permits it.

The version can be checked from the command line with:

```bash
python pdf_unredact.py --version
```

Expected output:

```text
pdf_unredact.py 1.0.0
```

---

## Web interface

Start the interface with:

```bash
python pdf_unredact.py --web
```

The app prefers `http://127.0.0.1:8765/`. If port `8765` is already occupied, it automatically selects a free local port and prints the actual address to open.

The interface is designed as the basis for the desktop application on Windows, macOS, and Linux, while keeping the same PDF-processing engine and workflow.


### Application information

The **ⓘ** button in the top bar opens an information dialog showing the application name, current version, and GitHub repository.

The project repository is:

https://github.com/andrea-del-sarto/pdf-unredact

The URL is centralized in `web_app.py` and can optionally be overridden with the `PDF_UNREDACT_REPOSITORY` environment variable for development or repackaging.

### Languages

The interface is available in **English** and **Italian**.

Use the language button in the top-right corner to switch between `IT` and `EN`. On first launch, the interface chooses Italian when the system/browser language starts with `it`; otherwise it defaults to English. The selected language is remembered for future launches.

The language switch updates the interface without reloading the application, including buttons, option descriptions, statistics labels, findings/status labels, preview accessibility text, theme tooltips, progress messages, export messages, and localized API errors. Number formatting also follows the selected language.

### Theme

The top-right theme button cycles through three states, each represented by a different icon:

- **System** — monitor icon; follows the operating system / desktop color preference through the standard `prefers-color-scheme` mechanism;
- **Light** — sun icon; forces the light theme;
- **Dark** — moon icon; forces the dark theme.

Each press advances `System → Light → Dark → System`. The button tooltip and accessible label always indicate the current state. `System` reacts to theme changes exposed by Windows, macOS, and Linux desktop/browser environments, while a manually selected Light or Dark preference is remembered locally.

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

The CLI-only `--port` and `--no-browser` flags control how the temporary web host is started and are therefore not document-processing settings inside the interface.

To use another port while running the browser version:

```bash
python pdf_unredact.py --web --port 9000
```

To prevent automatic browser opening:

```bash
python pdf_unredact.py --web --no-browser
```

## CLI

The command-line interface remains available:

```bash
python pdf_unredact.py document.pdf --mode clean
python pdf_unredact.py document.pdf --mode side_by_side
```

Analysis information can be printed or saved as JSON:

```bash
python pdf_unredact.py document.pdf --mode clean --stats
python pdf_unredact.py document.pdf --mode clean --stats --stats-json audit.json
```

By default only redaction-like annotations are removed. To deliberately remove all annotations:

```bash
python pdf_unredact.py document.pdf --mode clean --remove all-annotations
```

## Output behavior

### Clean PDF

`clean` starts from the source PDF and removes selected annotation-based covers without re-typesetting the visible text. The source PDF's text objects, embedded font resources, sizes, colors, positioning, images, and vector graphics are therefore retained wherever the PDF structure permits it.

### Side-by-side

The left half reproduces the visible original page, including annotation appearances. The right half contains cleaned native PDF content. Visible text on the right is not rebuilt using fallback fonts.

## Redaction classification

The analysis classifies detected candidates as:

- **Recoverable** — live PDF text overlaps the detected cover.
- **Probably applied** — a dark redaction-like rectangle is present but no live text is found underneath. This is heuristic and can include legitimate graphics.
- **Uncertain** — a redaction-like annotation exists but recoverable content cannot be established confidently.

The tool recognizes dedicated PDF Redact annotations and some improvised covers such as opaque black Highlight or Square annotations. It can also inspect dark rectangles in the page drawing stream for audit purposes.

## Important limitations

A properly applied PDF redaction normally removes the underlying content. If the content no longer exists in the PDF, `pdf-unredact` cannot reconstruct it.

The project does not perform OCR, does not bypass encryption or passwords, and should not be treated as proof that every dark graphical region is a redaction.

Editing or re-saving a PDF can affect digital signatures, certification state, incremental revisions, or evidentiary provenance. Always retain the original document unchanged.

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

## Fork / upstream

Upstream project: **leedrake5/unredact**  
Upstream repository: https://github.com/leedrake5/unredact  
License: **GNU GPLv3**

Version `1.0.0` is the first official commit/release of this fork. It establishes the new `pdf-unredact` codebase while retaining attribution to the GPLv3-licensed upstream project.

Major changes in this fork include native PDF-content preservation, `clean` export, selective redaction removal, expanded redaction analysis/classification, JSON reporting, a rewritten side-by-side mode, use of the `pymupdf` import name, and the redesigned bilingual interface with English/Italian localization and system/light/dark themes.

Parts of this fork were developed with AI-assisted coding tools ("vibe coding") as part of the implementation workflow. Changes were reviewed, tested, and adapted before inclusion.

## License

This project is licensed under the **GNU General Public License v3.0**. The full license text is included in [`LICENSE`](LICENSE).
