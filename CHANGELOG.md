# Changelog

All notable changes to `pdf-unredact` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-09-25

### Added

- Separate `removable` and `removal_method` fields for each redaction candidate so recoverability no longer implies that the exporter can remove the cover.
- `annotation_delete`, `content_stream_rewrite`, and `unsupported` removal methods in JSON reports and the web findings table.
- Conservative content-stream rewriting for isolated recoverable dark filled rectangles, allowing vector black bars embedded directly in page content to be removed without deleting the live text underneath.
- Removability metrics in the web interface, including recoverable-but-unsupported candidates.
- Export results now track annotation removals, content-stream rectangle removals, and unsupported recoverable rectangles internally.

### Changed

- Clean preview uses the same cleaning pipeline as final export, including supported content-stream rectangle removal.
- `side_by_side` right-hand output now applies supported content-stream rewrites as well as annotation removal.
- Replaced the misleading `recovery_rate` statistic with `hidden_text_percentage`, defined as the percentage of all extracted text characters that are located underneath detected covers.
- Redaction analysis documentation now distinguishes content recoverability from cover removability.
- Text-under-cover analysis now uses PyMuPDF directly; the redundant `pdfplumber` runtime dependency has been removed.
- Non-recoverable cases remain intentionally untouched: permanently applied redactions with deleted text, legitimate black graphics, and raster images with baked-in covers are not destructively rewritten.

### Fixed

- Fixed inherited transformation handling in content-stream rewriting: page-wide or outer `cm` matrices are now tracked across sequential `/Contents` streams and propagated through nested `q` / `Q` graphics-state blocks before rectangle geometry is matched.
- Fixed false-positive `dark_rectangle` detections caused by compound dark drawings such as table/grid borders: automatic cover detection now requires a single normalized rectangle drawing instead of treating the bounding box of multiple filled rectangles as one redaction.
- On the 72-page regression PDF, the previous 143 `recoverable` / `unsupported` findings are no longer reported as redactions because they are compound table/grid graphics; the 195 actual annotation-based covers remain recoverable and removable.
- Added safe handling for isolated rectangle covers drawn after PDF `cm` transformations; translation, scaling, rotation, and normal matrix concatenation are resolved before geometry matching.
- Added conservative support for simple `ExtGState` (`gs`) cover blocks when the referenced graphics state is near-opaque, uses normal blending, and has no unsupported soft mask or graphics-state features.
- Added support for rectangular paths constructed with `m` / `l` / `h` instead of the `re` operator, while continuing to reject curves and non-rectangular paths.
- Fixed `B` / `B*` cover blocks that also set simple stroke width or stroke graphics state before painting the rectangle.
- Added support for harmless `n` path resets emitted by common PDF generators before the actual cover path.
- Expanded the 1.2.0 content-stream regression coverage so the transform-matrix, fill-and-stroke, ExtGState, and explicit-path fixtures are now all classified as recoverable and removable when their blocks satisfy the conservative safety rules.
- Fixed recoverable content-stream rectangles generated with the PDF `B` / `B*` / `b` / `b*` fill-and-stroke operators; the stream rewriter now safely handles their stroke-color operators as part of the same isolated rectangle block.
- Fixed near-black vector covers such as RGB `0.12 0.12 0.12`, which were previously missed by the hard `< 0.10` darkness threshold. The conservative darkness threshold is now `<= 0.15`.
- Fixed a case where a recoverable `dark_rectangle` was detected in the audit but remained visible in `clean` and in the cleaned side of `side_by_side`.
- Fixed rotated-page analysis by using PyMuPDF word geometry in the same coordinate system as annotations and drawings.
- Fixed false-positive semantics for large dark graphics with no live text underneath: they are now left `uncertain` instead of automatically being labelled as probably applied redactions.
- Fixed the clean preview so it no longer disagrees with export for supported page-content rectangles.

## [1.1.2] - 2026-09-25

### Changed

- The web interface now starts the PDF download immediately after **Export PDF** finishes generating the document.
- Removed the redundant second download button from the export panel.

### Fixed

- Export no longer requires a second user action to download the generated PDF.

## [1.1.1] - 2026-09-25

### Fixed

- Suppressed Chrome/Chromium launcher warnings and "opening in existing browser session" messages when automatically opening the local web UI on Linux/macOS.
- Browser launch now uses a detached, silent platform-specific opener with a portable fallback.

## [1.1.0] - 2026-09-25

### Added

- External JSON translation catalogs in `locales/en.json` and `locales/it.json`.
- Shared localization source for both frontend interface strings and backend/API messages.
- `i18n.py` locale loader with English fallback and locale normalization.
- Runtime validation that English and Italian expose the same translation keys.
- Local `/api/i18n/<lang>` endpoints used by the frontend to load the selected language.
- Built-in `/favicon.ico` response so the local server no longer logs a favicon 404.

### Changed

- Removed hardcoded frontend and backend translation dictionaries from the application code.
- Updated language switching to use the external JSON catalogs without reloading the interface.
- Updated packaging metadata so locale JSON files and `i18n.py` are included in distributions.
- Reworked the README language documentation to describe the shared frontend/backend JSON localization system.
- Simplified README structure by removing redundant application-information and theme sections and consolidating licence/upstream information.

### Fixed

- Eliminated `/favicon.ico` 404 responses from the local web server logs.
- Missing or mismatched translation keys are now detected at startup instead of failing silently at runtime.

## [1.0.0] - 2026-09-25

### Added

- First official release of the `pdf-unredact` fork.
- Local graphical web interface intended as the foundation for future desktop builds.
- English and Italian interface localization.
- Shared JSON localization catalogs for both frontend and backend messages.
- Automatic locale-key validation to keep English and Italian catalogs synchronized.
- System, Light, and Dark themes with operating-system color preference detection.
- Application information dialog with app name, version, and GitHub repository.
- `clean` export mode.
- `side_by_side` comparison mode.
- Redaction analysis with `recoverable`, `probably applied`, and `uncertain` classifications.
- Detailed JSON analysis reports.
- Selective removal of redaction-like annotations.
- Optional removal of all annotations.
- Automatic fallback to a free local port when the preferred web port is already in use.
- CLI version reporting through `--version`.

### Changed

- Project renamed to `pdf-unredact`.
- PDF processing now preserves native PDF content instead of rebuilding visible text line by line.
- Original PDF fonts, font sizes, colors, text positioning, images, and vector graphics are preserved wherever the source PDF structure allows it.
- Migrated from the legacy `fitz` import name to `pymupdf`.
- Side-by-side output now uses cleaned native PDF content on the right-hand side.
- Default annotation removal is selective instead of removing unrelated annotations.

### Removed

- Legacy `overlay_white` mode.
- Legacy text reconstruction pipeline.
- Legacy font-matching and spacing reconstruction options.
- Example and development-only files not required by the distributable project.

### Fixed

- Web startup no longer fails when the default port is already occupied; a free port is selected automatically.
- Duplicate-line behavior from the original reconstruction pipeline was eliminated together with that obsolete pipeline.
- Output directories are created when needed.
- Input and output paths are prevented from being identical.

### Upstream

This project is a substantially modified fork of:

- `leedrake5/unredact`
- https://github.com/leedrake5/unredact

Project repository:

- https://github.com/andrea-del-sarto/pdf-unredact

The project remains licensed under the GNU General Public License v3.0.
