# Changelog

All notable changes to `pdf-unredact` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/).

## [1.5.0] - 2026-10-08

- Fixed the preview zoom indicator so it always shows the effective zoom level; the `+` / `−` controls support 25%–400%, while clicking the percentage resets to 100%.
- Fixed preview zoom geometry: fit-to-page now respects the available viewport/pane height, fit-to-width is recalculated from the actual pane width, and +/- zoom continues smoothly from the current fitted size.
- Moved page navigation controls onto the same toolbar row as PDF text search, keeping them vertically aligned with the search field.

- Anteprima: `Adatta pagina` e `Adatta larghezza` ora usano uno stato visivo attivo, con larghezza fissa dei controlli per evitare spostamenti al cambio lingua.

### Added

- Added full-document text search with previous/next result navigation and visual highlighting in the preview.
- Added preview zoom controls for zoom in/out, 100%, fit page, and fit width.
- Added 90° left/right preview rotation without modifying the source PDF.
- Added finding filters for recoverability, detection type, and page.
- Added previous/next finding navigation with automatic preview centering on the selected area.
- Added optional temporary finding highlighting in the preview.
- Added an export option that keeps only pages containing detected findings.
- Added recovered-text export in TXT, JSON, and CSV formats.
- Added visible progress reporting for analysis, preview, and export, including verification and atomic commit phases.
- Added PDF integrity reporting, including notification when MuPDF repairs a malformed document while opening it.
- Added an optional diagnostics panel, hidden by default, exposing engine/runtime information, configured limits, document integrity information, and processing timings.
- Added advanced detection rules for dark-fill threshold, minimum rectangle width/height, and minimum text-overlap ratio.

### Changed

- Preview generation now returns opaque artifact handles instead of exposing engine-owned temporary filesystem paths; the host reads and releases preview artifacts through `EngineClient`.
- Engine workspace accounting is now atomic across concurrent operations and includes staged inputs, persisted analyses, preview artifacts, and bounded transient worker results.
- Added explicit engine limits for input handles, output handles, analyses, and preview artifacts, with reservation slots preventing concurrent over-allocation.
- La card **Apri PDF** viene nascosta dopo il caricamento riuscito del documento; il cambio file resta disponibile dalla card del documento.
- Riordinata la colonna laterale: **Esporta → Formato di output → Annotazioni → Sicurezza → Regole di rilevamento avanzate**.

- Updated the English and Italian localization catalogs for all 1.5.0 controls, statuses, diagnostics, integrity messages, and advanced-detection settings.
- Kept preview-only operations such as zoom and rotation separate from PDF export, so visual inspection cannot alter the source or exported document unless an explicit export option requires it.

### Compatibility

- Preserves the 1.4.6 engine API, capability-handle model, atomic export, optional active-content sanitization, worker limits, fullscreen preview, and keyboard page navigation.

### Fixed

- Manual preview zoom above 100% now disables flex shrinking, so 125%–400% produces real enlargement with pane scrolling instead of being compressed back to the viewport.
- Restored the top-right information and theme icons to the 1.4.6 fixed rendering.
- Diagnostics toggle now reflects the active state (enable/disable).
- Recovered-text TXT/JSON/CSV exports now use the native save-location dialog when supported, matching PDF export behavior.

## [1.4.6] - 2026-10-05

### Added

- Added keyboard navigation in the preview: Left Arrow shows the previous PDF page and Right Arrow shows the next page, including while the preview is in full-screen mode. Keyboard navigation is ignored while typing in editable controls.

### Changed

- Updated the Italian and English preview descriptions to expose the new keyboard shortcuts.

### Fixed

- Treat normal browser disconnects during responses, previews, and downloads (`BrokenPipeError`, `ConnectionResetError`, `ConnectionAbortedError`) as expected client cancellation instead of printing server tracebacks.

## [1.4.5] - 2026-10-05

### Added

- Added a versioned frontend-neutral engine contract (`ENGINE_API_VERSION = 1`) and `api_info()` capability discovery for the future Tauri/Flutter bridge.
- Added cancellable operation handles for audit, preview, export, and probe. Frontends can create an operation, pass its opaque ID to the engine call, and cancel it without knowing worker-process details.
- Added structured progress events with stable fields (`operation_id`, operation, phase, current, total) and no dependency on the legacy web UI.
- Added stable engine error codes and serializable `EngineError` payloads so desktop frontends do not need to parse Python exception text.
- Added a generic idempotent `release(handle)` lifecycle primitive for input, output, analysis, and operation capabilities.

### Changed

- Worker cancellation is now checked by the parent while the subprocess is running and terminates the worker process group on request.
- Audit, preview, export, and probe responses/events expose the engine API version where applicable.
- Public engine validation failures now use stable error codes for invalid handles, invalid input, size/page limits, worker timeouts/crashes, cancellation, workspace exhaustion, and export failures.

### Security / hardening

- Cancellation remains parent-controlled: untrusted PDF code cannot decide whether an operation is considered cancelled.
- Operation handles are opaque, single-operation capabilities and cannot be reused concurrently.
- Progress callbacks are isolated from engine execution: callback failures cannot abort or alter PDF processing.

## [1.4.4] - 2026-10-05

### Added

- Added capability-based input/output handles to the frontend-neutral engine API. Frontends no longer provide authorization-root lists to approve their own filesystem requests.
- Added immutable input staging: user-selected PDFs are copied into an engine-owned private workspace before audit, preview, or export, with staging covered by the engine workspace quota.
- Added atomic export commits. Workers write to a random temporary file on the destination filesystem; the final destination is replaced only after successful processing and validation.
- Extended sanitized-export verification to XFA, JavaScript name trees, RichMedia/Screen/file-attachment annotations, and additional executable/external action types.

### Changed

- Preview generation now writes only to engine-owned temporary storage and no longer accepts caller-selected output paths.
- PyMuPDF is pinned to `1.26.7` in both project metadata and release constraints; Hatchling remains pinned to `1.27.0` for reproducible release builds.
- Updated the web host and CLI to use opaque engine capabilities instead of passing input/output authorization paths.

### Security

- Removed the caller-controlled `authorized_output_roots` authorization model from the public engine API.
- Eliminated the audit/preview/export TOCTOU window caused by repeatedly reopening a mutable user path.
- Failed exports cannot truncate or partially replace an existing destination PDF.
- Sanitized exports fail closed when extended active-content checks cannot prove the saved PDF free of the supported active-content classes.

## [1.4.3] - 2026-10-04

### Changed
- Added a localized full-screen control to the preview toolbar, next to page navigation.
- Renamed the findings section to “Dettagli rilevamenti” / “Finding details”.
- Finding details are collapsed by default and can be expanded on demand.
- Updated Italian and English UI translations for the new controls.

## [1.4.2] - 2026-10-04

### Added

- Added an explicit export setting to enable or disable active-content sanitization. Sanitization remains enabled by default in the web UI, CLI, and engine API.
- Added post-export fail-closed verification for sanitized PDFs. Requested sanitized exports are rejected and deleted if embedded files, links, automatic actions, JavaScript/Launch-style actions, or other verified action entry points remain.
- Added opaque engine analysis handles so frontends no longer pass audit JSON paths into the engine API.
- Added release constraints pinning the tested PyMuPDF runtime and Hatchling build backend.

### Changed

- Enforced `max_pages` inside every worker operation that opens a user PDF: audit, preview, and export.
- Moved engine temporary directories and audit artifacts fully inside `EngineClient`; public callers no longer supply `work_dir` or `stats_path`.
- CLI processing now relies entirely on engine-owned temporary storage.

### Security

- Sanitized exports now fail closed if post-save verification cannot prove active-content removal.
- Analysis handles are bound to input-file metadata so a changed input cannot reuse stale audit findings.

## [1.4.1] - 2026-10-04

### Added

- Added export-time active-content sanitization. Clean and side-by-side PDFs strip JavaScript, active links/actions, embedded/attached files, and interactive response state while explicitly preserving hidden text and redaction content needed by the recovery workflow.
- Added a frontend-neutral `engine_api.py` security boundary with validated operations for audit, preview, and export. The CLI and web host now use the same API, intended to be reusable by future Tauri or Flutter frontends.
- Added hard input/output size enforcement inside both the engine API and worker, including a worker file-size ceiling and post-export size validation.
- Added workspace quotas, free-disk headroom checks, and a bounded number of active web jobs.
- Added regression tests covering active-content stripping, preservation of recoverable hidden text, input-size enforcement, and authorized output paths.

### Changed

- Reviewed and aligned English/Italian UI terminology, localized the Side-by-side mode label, and removed mixed-language labels from the interface.
- Locale validation now also verifies format placeholders, preventing runtime formatting errors caused by inconsistent translations.
- Moved PDF limits and worker invocation details out of the web-specific layer into `EngineLimits` / `EngineClient` so security policy is independent of the current HTTP frontend.
- CLI export now uses the same active-content sanitization and path/size validation as graphical exports.
- Worker output limits are derived from the configured engine policy rather than a web-only code path.

### Security

- Exported PDFs are sanitized by default. PyMuPDF `scrub()` is called with `hidden_text=False` and `redactions=False` so the security pass does not destroy recoverable content.
- Preview and export outputs must remain inside explicitly authorized output roots supplied by the host application.
- Temporary workspace growth is bounded by `PDF_UNREDACT_MAX_WORKSPACE_MB`, while `PDF_UNREDACT_MIN_FREE_MB` preserves disk headroom before processing.

## [1.4.0] - 2026-10-02

### Added

- Added a short-lived isolated PDF worker process for web audits, previews, and exports so untrusted PDF parsing no longer occurs inside the HTTP server process.
- Added a parent-side resident-memory (RSS) monitor for worker processes on Linux, macOS, and Windows. The default cap is 1536 MB and is configurable with `PDF_UNREDACT_WORKER_MAX_RSS_MB`; `0` disables it.
- Added worker execution timeouts, bounded PDF-processing concurrency, page-count and preview-pixel limits, and best-effort CPU, file-size, file-descriptor, and Linux address-space limits.
- Added a random per-launch session secret. The initial local URL includes the secret once, the server exchanges it for an HttpOnly `SameSite=Strict` session cookie, and the frontend removes the token from the visible URL after startup.
- Added Host and same-origin validation for local API requests to reduce localhost / DNS-rebinding and cross-origin request exposure.
- Added per-job locks for audit, preview, export, report, download, and cleanup operations.
- Added a job cleanup API used when a document is replaced or the UI page is closed.
- Added stale temporary-directory cleanup at startup plus `atexit`, SIGINT, and SIGTERM cleanup paths for document data and isolated browser profiles.
- Added stricter HTTP security headers including `Referrer-Policy`, `Cross-Origin-Resource-Policy`, `Cross-Origin-Opener-Policy`, `Permissions-Policy`, and `X-Frame-Options` defense in depth.

### Changed

- CLI audit and export operations now use the same short-lived PDF worker as the web interface instead of parsing untrusted PDFs directly in the main CLI process.
- Worker subprocesses now inherit a narrower environment and no longer inherit `LD_LIBRARY_PATH`, `DYLD_LIBRARY_PATH`, `PYTHONPATH`, or `PYTHONHOME`.
- Uploads are now streamed to a private temporary file in chunks instead of being read completely into memory before analysis.
- The default web upload limit is now 250 MB and can be overridden with `PDF_UNREDACT_MAX_UPLOAD_MB`; JSON request bodies are separately size-limited.
- Browser-facing failures now return generic localized messages while technical details stay in local logs; set `PDF_UNREDACT_DEBUG=1` for verbose worker diagnostics.
- Frontend JavaScript and CSS are now served as same-origin static resources, allowing the Content Security Policy to remove `unsafe-inline` from both `script-src` and `style-src`.
- PDF downloads are streamed rather than loaded completely into server memory and the temporary exported copy is removed after delivery.
- Linux browser discovery now searches trusted installation directories instead of executing an arbitrary browser found through the current `PATH`; automatic browser launch is disabled when the app is run as root.
- Runtime version metadata is centralized in `version.py`, and package metadata now includes the isolated worker and static web assets.

### Fixed

- Reduced the risk of memory exhaustion from large uploads, oversized previews, parallel PDF work, and oversized API request bodies.
- Prevented concurrent operations on the same job from racing while rewriting or deleting temporary files.
- Prevented raw parser exceptions and local filesystem details from being returned directly to the web interface.
- Hardened temporary file permissions on supported platforms and cleanup behavior after normal shutdown, signals, document replacement, and later restarts after an unclean exit.
- Stale temporary cleanup now requires an application-owned marker file before removing a directory, reducing the chance of deleting an unrelated temporary directory that only shares the same prefix.

### Fixed
- Fixed worker startup for source installations where PyMuPDF is installed in the same interpreter's user-site packages. The web worker no longer uses Python isolated mode (`-I`) or suppresses the user-site while still receiving a strict allow-listed environment with `PYTHONPATH` / `PYTHONHOME` excluded.
- Worker import/bootstrap failures now write a structured result when possible, so missing or incompatible dependencies are diagnosed instead of being reported only as an unexpected worker termination.
- Disabled the Linux `RLIMIT_AS` memory cap by default. Virtual-address-space limits can terminate PyMuPDF/MuPDF workers even when real memory use is modest; deployments may still opt in with `PDF_UNREDACT_WORKER_MEMORY_MB`.
- Worker failures that occur before a result file is produced are now diagnosed explicitly in local logs instead of being indistinguishable from normal PDF-processing errors.
- Added a lightweight worker probe used to verify that the isolated PyMuPDF engine can start successfully.

## [1.3.0] - 2026-10-01

### Added

- Added screen-sized/maximized startup for the isolated local web UI without entering true browser full-screen. Chromium-family browsers use app mode with maximized startup; Firefox-family browsers use a normal isolated private window sized to the detected screen as a best-effort cross-platform equivalent. Window decorations and normal window controls remain available.
- Added isolated launcher support for Firefox and compatible Firefox-derived browsers on Windows, macOS, and Linux.
- Added discovery for common Chromium derivatives including Brave, Vivaldi, and Opera, plus Firefox derivatives such as LibreWolf, Waterfox, Floorp, and Zen where installed.

### Changed

- Browser startup now tries compatible isolated browsers in priority order instead of being limited to Chrome/Chromium/Edge.
- Chromium sessions continue to use an ephemeral `--user-data-dir`; Firefox-family sessions use an ephemeral `-profile` together with `-no-remote` / `-new-instance`.
- The operating system default browser is still never opened automatically, and browser security sandboxes remain enabled.
- Safari is intentionally not auto-launched on macOS because it does not provide a supported command-line mechanism for the disposable isolated profile required by this launcher.

## [1.2.1] - 2026-09-27

### Changed

- The local web UI no longer opens the operating system default browser automatically. It now launches a dedicated Chromium-family app window with an ephemeral isolated profile when Chrome, Chromium, or Edge is available.
- The browser security sandbox remains enabled; `pdf-unredact` does not pass `--no-sandbox`. If no supported Chromium-family browser is found, the server prints the local URL instead of falling back to the user's default browser.
- Export diagnostics now distinguish recoverable-but-unsupported rectangles from content-stream rewrite failures.
- `CleanResult` now reports `rewrite_attempts`, `rewrite_successes`, and `rewrite_failures` separately from `unsupported_recoverable_rectangles`.
- Clean and side-by-side exports now validate that the saved PDF can be reopened and that the expected page count is preserved.

### Fixed

- Fixed export accounting where a candidate that was marked `content_stream_rewrite` during audit but failed to match again during export was incorrectly counted as `unsupported`.
- Removed a duplicate internal recoverability assignment in text-under-cover analysis.
- Hardened export failure reporting so rewrite mismatches are visible as execution failures instead of being conflated with unsupported PDF structures.

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
