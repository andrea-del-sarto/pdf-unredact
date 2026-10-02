"""Isolated PDF worker for the local web UI.

The web server never parses untrusted PDF data directly. Each audit, preview,
and export is delegated to a short-lived subprocess with best-effort resource
limits. The CLI remains available through pdf_unredact.py.
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
        _candidate_from_dict,
        _clean_document,
        compute_redaction_stats,
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


def cmd_audit(args) -> None:
    doc = pymupdf.open(args.input)
    try:
        if doc.needs_pass:
            raise ValueError("password-protected PDF")
        pages = doc.page_count
        if pages <= 0:
            raise ValueError("PDF contains no pages")
        if args.max_pages and pages > args.max_pages:
            raise ValueError(f"PDF has too many pages ({pages} > {args.max_pages})")
    finally:
        doc.close()
    stats = compute_redaction_stats(args.input)
    _write_json(args.result, {"ok": True, "pages": pages, "stats": stats.to_dict()})


def cmd_preview(args) -> None:
    doc = pymupdf.open(args.input)
    try:
        if args.page < 1 or args.page > doc.page_count:
            raise ValueError("invalid page")
        if args.kind == "clean":
            stats_dict = json.loads(Path(args.stats).read_text(encoding="utf-8"))
            findings = [_candidate_from_dict(x) for x in stats_dict.get("findings", [])]
            _clean_document(doc, findings, remove=args.remove, page_numbers=[args.page])
        page = doc[args.page - 1]
        # Bound rendered pixels independently of page dimensions.
        zoom = 1.35
        width = max(1, int(page.rect.width * zoom))
        height = max(1, int(page.rect.height * zoom))
        if width * height > args.max_pixels:
            scale = (args.max_pixels / float(width * height)) ** 0.5
            zoom *= scale
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True)
        pix.save(args.output)
    finally:
        doc.close()
    _write_json(args.result, {"ok": True})


def cmd_probe(args) -> None:
    # Importing this module has already exercised PyMuPDF and the engine imports.
    _write_json(args.result, {"ok": True, "python": sys.version.split()[0], "pymupdf": getattr(pymupdf, "__version__", "unknown")})


def cmd_export(args) -> None:
    if args.mode == "clean":
        result = make_clean_pdf(args.input, args.output, remove=args.remove)
    else:
        result = make_side_by_side(args.input, args.output, remove=args.remove)
    _write_json(args.result, {"ok": True, "clean_result": result.to_dict()})


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    sub = ap.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe")
    probe.add_argument("--result", required=True)
    probe.set_defaults(func=cmd_probe)

    audit = sub.add_parser("audit")
    audit.add_argument("--input", required=True)
    audit.add_argument("--result", required=True)
    audit.add_argument("--max-pages", type=int, default=2000)
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
    preview.set_defaults(func=cmd_preview)

    export = sub.add_parser("export")
    export.add_argument("--input", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--result", required=True)
    export.add_argument("--mode", choices=["clean", "side_by_side"], required=True)
    export.add_argument("--remove", choices=["redactions", "all-annotations"], default="redactions")
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
