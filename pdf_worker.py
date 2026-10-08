"""Isolated PDF worker for the local web UI.

The web host and CLI do not parse untrusted PDF data directly. Audit, preview,
and export operations are delegated to a short-lived subprocess with best-effort
resource limits and parent-side RSS monitoring.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _apply_resource_limits() -> None:
    if os.name != "posix":
        return
    try:
        import resource
    except Exception:
        return

    cpu = _int_env("PDF_UNREDACT_WORKER_CPU_SECONDS", 180)
    mem_mb = _int_env("PDF_UNREDACT_WORKER_MEMORY_MB", 0)
    file_mb = _int_env("PDF_UNREDACT_WORKER_FILE_MB", 2048)
    nofile = _int_env("PDF_UNREDACT_WORKER_NOFILE", 256)

    def set_limit(kind, soft: int, hard: int | None = None) -> None:
        try:
            current_soft, current_hard = resource.getrlimit(kind)
            wanted_hard = soft if hard is None else hard
            if current_hard != resource.RLIM_INFINITY:
                wanted_hard = min(wanted_hard, current_hard)
            wanted_soft = min(soft, wanted_hard)
            resource.setrlimit(kind, (wanted_soft, wanted_hard))
        except Exception:
            pass

    if cpu:
        set_limit(resource.RLIMIT_CPU, cpu, cpu + 5)
    if file_mb:
        set_limit(resource.RLIMIT_FSIZE, file_mb * 1024 * 1024)
    if nofile:
        set_limit(resource.RLIMIT_NOFILE, nofile)
    # Address-space limits are intentionally opt-in. RLIMIT_AS measures virtual
    # address space rather than resident memory and can terminate native PDF
    # libraries even when actual RAM use is modest. Enable explicitly with
    # PDF_UNREDACT_WORKER_MEMORY_MB when a deployment has validated the limit.
    if mem_mb and sys.platform.startswith("linux"):
        set_limit(resource.RLIMIT_AS, mem_mb * 1024 * 1024)


_apply_resource_limits()

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _write_json(path: str, obj: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def _bootstrap_result_path() -> str | None:
    # Imports happen before argparse runs. Recover --result directly so startup
    # failures (for example a missing PyMuPDF in the worker interpreter) are
    # still returned as structured diagnostics instead of looking like a crash.
    try:
        index = sys.argv.index("--result")
        return sys.argv[index + 1]
    except (ValueError, IndexError):
        return None


try:
    import pymupdf  # noqa: E402
    from pdf_unredact import (  # noqa: E402
        DetectionRules,
        _candidate_from_dict,
        _clean_document,
        compute_redaction_stats,
        extract_recovered_text,
        make_clean_pdf,
        make_side_by_side,
    )
except BaseException as exc:
    result_path = _bootstrap_result_path()
    if result_path:
        try:
            _write_json(result_path, {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": f"worker bootstrap failed: {exc}",
            })
        except Exception:
            pass
    raise SystemExit(3)


def _validate_input_file(path: str, max_bytes: int) -> Path:
    target = Path(path).resolve()
    if not target.is_file():
        raise ValueError("input PDF does not exist or is not a regular file")
    size = target.stat().st_size
    if size <= 0 or (max_bytes and size > max_bytes):
        raise ValueError("input PDF exceeds the configured size limit")
    with target.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            raise ValueError("input is not a PDF file")
    return target


def _validate_output_file(path: str, max_bytes: int) -> Path:
    target = Path(path).resolve()
    if not target.is_file():
        raise RuntimeError("worker output was not generated")
    if max_bytes and target.stat().st_size > max_bytes:
        target.unlink(missing_ok=True)
        raise RuntimeError("worker output exceeds the configured size limit")
    return target


def _rules_from_args(args) -> DetectionRules:
    return DetectionRules(
        darkness_threshold=getattr(args, "darkness_threshold", 0.15),
        min_width=getattr(args, "min_width", 10.0),
        min_height=getattr(args, "min_height", 5.0),
        word_overlap_threshold=getattr(args, "word_overlap_threshold", 0.5),
    ).validated()


def _parse_pages(value: str | None) -> list[int] | None:
    if not value:
        return None
    pages = sorted({int(part) for part in value.split(",") if part.strip()})
    return pages or None


def _integrity_info(doc) -> dict:
    warnings = []
    try:
        raw = pymupdf.TOOLS.mupdf_warnings()
        if raw:
            warnings = [line.strip() for line in str(raw).splitlines() if line.strip()][:20]
    except Exception:
        pass
    return {
        "repaired": bool(getattr(doc, "is_repaired", False)),
        "warnings": warnings,
        "encrypted": bool(getattr(doc, "needs_pass", False)),
    }


def cmd_audit(args) -> None:
    input_path = _validate_input_file(args.input, args.max_input_bytes)
    doc = pymupdf.open(input_path)
    try:
        integrity = _integrity_info(doc)
        if doc.needs_pass:
            raise ValueError("password-protected PDF")
        pages = doc.page_count
        if pages <= 0:
            raise ValueError("PDF contains no pages")
        if args.max_pages and pages > args.max_pages:
            raise ValueError(f"PDF has too many pages ({pages} > {args.max_pages})")
    finally:
        doc.close()
    stats = compute_redaction_stats(str(input_path), _rules_from_args(args))
    _write_json(args.result, {"ok": True, "pages": pages, "stats": stats.to_dict(), "integrity": integrity})


def _validate_page_count(doc, max_pages: int) -> int:
    if doc.needs_pass:
        raise ValueError("password-protected PDF")
    pages = doc.page_count
    if pages <= 0:
        raise ValueError("PDF contains no pages")
    if max_pages and pages > max_pages:
        raise ValueError(f"PDF has too many pages ({pages} > {max_pages})")
    return pages


def cmd_preview(args) -> None:
    input_path = _validate_input_file(args.input, args.max_input_bytes)
    doc = pymupdf.open(input_path)
    try:
        pages = _validate_page_count(doc, args.max_pages)
        if args.page < 1 or args.page > pages:
            raise ValueError("invalid page")
        stats_dict = json.loads(Path(args.stats).read_text(encoding="utf-8"))
        findings = [_candidate_from_dict(x) for x in stats_dict.get("findings", [])]
        if args.kind == "clean":
            _clean_document(doc, findings, remove=args.remove, page_numbers=[args.page])
        page = doc[args.page - 1]
        # Temporary visual overlays are annotations on the in-memory preview only.
        if args.highlight_findings:
            for finding in findings:
                if finding.page != args.page:
                    continue
                annot = page.add_rect_annot(pymupdf.Rect(*finding.box))
                annot.set_colors(stroke=(1.0, 0.55, 0.0))
                annot.set_border(width=1.5)
                annot.update(opacity=0.85)
        if args.search_query:
            for rect in page.search_for(args.search_query)[:250]:
                annot = page.add_highlight_annot(rect)
                annot.set_colors(stroke=(1.0, 0.85, 0.0))
                annot.update(opacity=0.45)
        if args.focus_box:
            values = [float(v) for v in args.focus_box.split(",")]
            if len(values) == 4:
                annot = page.add_rect_annot(pymupdf.Rect(*values))
                annot.set_colors(stroke=(1.0, 0.15, 0.12))
                annot.set_border(width=3)
                annot.update(opacity=1.0)
        zoom = 1.35
        width = max(1, int(page.rect.width * zoom))
        height = max(1, int(page.rect.height * zoom))
        if width * height > args.max_pixels:
            scale = (args.max_pixels / float(width * height)) ** 0.5
            zoom *= scale
        matrix = pymupdf.Matrix(zoom, zoom).prerotate(args.rotate)
        pix = page.get_pixmap(matrix=matrix, alpha=False, annots=True)
        pix.save(args.output)
    finally:
        doc.close()
    _write_json(args.result, {"ok": True})


def cmd_search(args) -> None:
    input_path = _validate_input_file(args.input, args.max_input_bytes)
    query = str(args.query or "").strip()
    if not query or len(query) > 256:
        raise ValueError("invalid search query")
    doc = pymupdf.open(input_path)
    matches = []
    try:
        pages = _validate_page_count(doc, args.max_pages)
        for page_no, page in enumerate(doc, 1):
            for rect in page.search_for(query):
                matches.append({
                    "page": page_no,
                    "box": [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
                    "page_width": float(page.rect.width),
                    "page_height": float(page.rect.height),
                })
                if len(matches) >= args.max_matches:
                    break
            if len(matches) >= args.max_matches:
                break
    finally:
        doc.close()
    _write_json(args.result, {"ok": True, "query": query, "pages": pages, "matches": matches, "truncated": len(matches) >= args.max_matches})


def cmd_extract_text(args) -> None:
    input_path = _validate_input_file(args.input, args.max_input_bytes)
    stats_dict = json.loads(Path(args.stats).read_text(encoding="utf-8"))
    findings = [_candidate_from_dict(x) for x in stats_dict.get("findings", [])]
    entries = extract_recovered_text(str(input_path), findings)
    _write_json(args.result, {"ok": True, "entries": entries})

def cmd_probe(args) -> None:
    # Importing this module has already exercised PyMuPDF and the engine imports.
    _write_json(args.result, {"ok": True, "python": sys.version.split()[0], "pymupdf": getattr(pymupdf, "__version__", "unknown")})


def cmd_export(args) -> None:
    input_path = _validate_input_file(args.input, args.max_input_bytes)
    probe = pymupdf.open(input_path)
    try:
        _validate_page_count(probe, args.max_pages)
    finally:
        probe.close()
    sanitize = bool(args.sanitize_active_content)
    page_numbers = _parse_pages(args.pages)
    rules = _rules_from_args(args)
    if args.mode == "clean":
        result = make_clean_pdf(
            str(input_path), args.output, remove=args.remove,
            sanitize_active_content=sanitize, page_numbers=page_numbers, detection_rules=rules,
        )
    else:
        result = make_side_by_side(
            str(input_path), args.output, remove=args.remove,
            sanitize_active_content=sanitize, page_numbers=page_numbers, detection_rules=rules,
        )
    _validate_output_file(args.output, args.max_output_bytes)
    _write_json(args.result, {"ok": True, "clean_result": result.to_dict()})


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    sub = ap.add_subparsers(dest="command", required=True)

    def add_detection_args(parser):
        parser.add_argument("--darkness-threshold", type=float, default=0.15)
        parser.add_argument("--min-width", type=float, default=10.0)
        parser.add_argument("--min-height", type=float, default=5.0)
        parser.add_argument("--word-overlap-threshold", type=float, default=0.5)

    probe = sub.add_parser("probe")
    probe.add_argument("--result", required=True)
    probe.set_defaults(func=cmd_probe)

    audit = sub.add_parser("audit")
    audit.add_argument("--input", required=True)
    audit.add_argument("--result", required=True)
    audit.add_argument("--max-pages", type=int, default=2000)
    audit.add_argument("--max-input-bytes", type=int, default=250 * 1024 * 1024)
    add_detection_args(audit)
    audit.set_defaults(func=cmd_audit)

    preview = sub.add_parser("preview")
    preview.add_argument("--input", required=True)
    preview.add_argument("--output", required=True)
    preview.add_argument("--result", required=True)
    preview.add_argument("--stats", required=True)
    preview.add_argument("--page", type=int, required=True)
    preview.add_argument("--kind", choices=["original", "clean"], required=True)
    preview.add_argument("--remove", choices=["redactions", "all-annotations"], default="redactions")
    preview.add_argument("--max-pixels", type=int, default=25_000_000)
    preview.add_argument("--max-pages", type=int, default=2000)
    preview.add_argument("--max-input-bytes", type=int, default=250 * 1024 * 1024)
    preview.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0)
    preview.add_argument("--search-query", default="")
    preview.add_argument("--highlight-findings", action="store_true")
    preview.add_argument("--focus-box", default="")
    preview.set_defaults(func=cmd_preview)

    search = sub.add_parser("search")
    search.add_argument("--input", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--max-matches", type=int, default=2000)
    search.add_argument("--max-pages", type=int, default=2000)
    search.add_argument("--max-input-bytes", type=int, default=250 * 1024 * 1024)
    search.add_argument("--result", required=True)
    search.set_defaults(func=cmd_search)

    extract = sub.add_parser("extract-text")
    extract.add_argument("--input", required=True)
    extract.add_argument("--stats", required=True)
    extract.add_argument("--max-input-bytes", type=int, default=250 * 1024 * 1024)
    extract.add_argument("--result", required=True)
    extract.set_defaults(func=cmd_extract_text)

    export = sub.add_parser("export")
    export.add_argument("--input", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--result", required=True)
    export.add_argument("--mode", choices=["clean", "side_by_side"], required=True)
    export.add_argument("--remove", choices=["redactions", "all-annotations"], default="redactions")
    export.add_argument("--max-pages", type=int, default=2000)
    export.add_argument("--max-input-bytes", type=int, default=250 * 1024 * 1024)
    export.add_argument("--max-output-bytes", type=int, default=1024 * 1024 * 1024)
    export.add_argument("--pages", default="")
    add_detection_args(export)
    sanitize_group = export.add_mutually_exclusive_group()
    sanitize_group.add_argument("--sanitize-active-content", dest="sanitize_active_content", action="store_true")
    sanitize_group.add_argument("--no-sanitize-active-content", dest="sanitize_active_content", action="store_false")
    export.set_defaults(sanitize_active_content=True)
    export.set_defaults(func=cmd_export)

    args = ap.parse_args()
    try:
        args.func(args)
        return 0
    except BaseException as exc:
        try:
            _write_json(getattr(args, "result", "worker-result.json"), {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
