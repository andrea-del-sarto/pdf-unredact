# Changelog

All notable changes to `pdf-unredact` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-25

### Added

- First official release of the `pdf-unredact` fork.
- Local graphical web interface intended as the foundation for future desktop builds.
- English and Italian interface localization.
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
