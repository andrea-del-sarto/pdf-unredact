from __future__ import annotations

import atexit
import hmac
import json
import locale
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from i18n import frontend_catalog, normalize_language, translate
from version import __version__
from engine_api import EngineClient, EngineLimits, directory_size, ensure_free_space


def _system_language():
    try:
        return normalize_language(locale.getlocale()[0])
    except Exception:
        return "en"


HOST = "127.0.0.1"
APP_REPOSITORY = os.environ.get("PDF_UNREDACT_REPOSITORY", "https://github.com/andrea-del-sarto/pdf-unredact")
ENGINE_LIMITS = EngineLimits.from_env()
MAX_UPLOAD_BYTES = ENGINE_LIMITS.max_input_bytes
MAX_JSON_BYTES = 64 * 1024
MAX_RESULT_JSON_BYTES = ENGINE_LIMITS.max_result_bytes
MAX_PAGES = ENGINE_LIMITS.max_pages
MAX_PREVIEW_PIXELS = ENGINE_LIMITS.max_preview_pixels
JOB_TTL_SECONDS = int(os.environ.get("PDF_UNREDACT_JOB_TTL_SECONDS", str(2 * 60 * 60)))
MAX_CONCURRENT_PDF_TASKS = max(1, int(os.environ.get("PDF_UNREDACT_MAX_WORKERS", "2")))
MAX_ACTIVE_JOBS = max(1, int(os.environ.get("PDF_UNREDACT_MAX_ACTIVE_JOBS", "16")))
MAX_WORKSPACE_BYTES = max(MAX_UPLOAD_BYTES, int(os.environ.get("PDF_UNREDACT_MAX_WORKSPACE_MB", "2048")) * 1024 * 1024)
DEBUG = os.environ.get("PDF_UNREDACT_DEBUG", "").lower() in {"1", "true", "yes"}
SESSION_TOKEN = secrets.token_urlsafe(32)
COOKIE_NAME = "pdf_unredact_session"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
WORKER_PATH = BASE_DIR / "pdf_worker.py"
TEMP_MARKER = ".pdf-unredact-workdir"
WORK_ROOT = Path(tempfile.mkdtemp(prefix="pdf-unredact-"))
try:
    os.chmod(WORK_ROOT, 0o700)
except OSError:
    pass
try:
    (WORK_ROOT / TEMP_MARKER).write_text("pdf-unredact\n", encoding="ascii")
except OSError:
    pass
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.RLock()
PDF_TASKS = threading.BoundedSemaphore(MAX_CONCURRENT_PDF_TASKS)
WORKSPACE_LOCK = threading.RLock()
WORKSPACE_RESERVED_BYTES = 0
WORKSPACE_RESERVED_JOBS = 0
BROWSER_PROFILES: set[str] = set()
BROWSER_PROFILES_LOCK = threading.Lock()
SHUTTING_DOWN = False
PROGRESS: dict[str, dict] = {}
PROGRESS_ALIASES: dict[str, str] = {}
PROGRESS_LOCK = threading.RLock()

def _engine_progress(event):
    with PROGRESS_LOCK:
        key = PROGRESS_ALIASES.get(event.operation_id, event.operation_id)
        PROGRESS[key] = event.to_dict()

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO, format="[%(levelname)s] %(message)s")
LOG = logging.getLogger("pdf-unredact")
ENGINE = EngineClient(
    limits=ENGINE_LIMITS,
    concurrency_guard=PDF_TASKS,
    log_debug=(lambda msg: LOG.debug("worker stderr: %s", msg)) if DEBUG else None,
    log_error=lambda msg: LOG.error("%s", msg),
    progress_callback=_engine_progress,
)

FAVICON_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#17191c"/><text x="32" y="40" text-anchor="middle" font-family="Arial,sans-serif" font-size="25" font-weight="700" fill="white">PU</text></svg>'


def _message(key, lang="en"):
    return translate(key, lang, section="backend")


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _safe_filename(name):
    name = os.path.basename(name or "document.pdf")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    cleaned = "".join(c for c in name if c.isalnum() or c in " ._-").strip()
    return cleaned or "document.pdf"


def _secure_job_dir(job_id: str) -> Path:
    path = WORK_ROOT / job_id
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _is_within(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _cleanup_stale_temp(prefix: str, older_than: int = 24 * 60 * 60) -> None:
    temp_root = Path(tempfile.gettempdir())
    now = time.time()
    try:
        entries = list(temp_root.glob(prefix + "*"))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.resolve() == WORK_ROOT.resolve() or not entry.is_dir() or now - entry.stat().st_mtime < older_than:
                continue
            if not (entry / TEMP_MARKER).is_file():
                continue
            shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def _cleanup_all() -> None:
    global SHUTTING_DOWN
    if SHUTTING_DOWN:
        return
    SHUTTING_DOWN = True
    with JOBS_LOCK:
        metas = list(JOBS.values())
        JOBS.clear()
    for meta in metas:
        ENGINE.close_analysis(meta.get("analysis_id"))
        ENGINE.close_input(meta.get("input_handle"))
        shutil.rmtree(meta.get("dir", ""), ignore_errors=True)
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    with BROWSER_PROFILES_LOCK:
        profiles = list(BROWSER_PROFILES)
        BROWSER_PROFILES.clear()
    for profile in profiles:
        shutil.rmtree(profile, ignore_errors=True)
    ENGINE.close()


atexit.register(_cleanup_all)


def _cleanup_old_jobs():
    now = time.time()
    expired = []
    with JOBS_LOCK:
        for job_id, meta in list(JOBS.items()):
            if now - meta["created"] <= JOB_TTL_SECONDS:
                continue
            lock = meta["lock"]
            if not lock.acquire(blocking=False):
                continue
            try:
                removed = JOBS.pop(job_id, None)
                if removed:
                    expired.append(removed)
            finally:
                lock.release()
    for meta in expired:
        ENGINE.close_analysis(meta.get("analysis_id"))
        ENGINE.close_input(meta.get("input_handle"))
        shutil.rmtree(meta["dir"], ignore_errors=True)


def _job(job_id):
    _cleanup_old_jobs()
    if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        return None
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _progress_id(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).replace("-", "").lower()
    return value if len(value) == 32 and all(c in "0123456789abcdef" for c in value) else None


def _run_engine_operation(kind: str, client_progress_id: str | None, callback):
    op_id = ENGINE.create_operation(kind)
    progress_id = _progress_id(client_progress_id)
    if progress_id:
        with PROGRESS_LOCK:
            PROGRESS_ALIASES[op_id] = progress_id
            PROGRESS[progress_id] = {"operation": kind, "phase": "queued", "current": None, "total": None}
    try:
        return callback(op_id)
    finally:
        ENGINE.close_operation(op_id)
        with PROGRESS_LOCK:
            PROGRESS_ALIASES.pop(op_id, None)


def _detection_rules_from(data: dict | None) -> dict:
    data = data or {}
    return {
        "darkness_threshold": data.get("darkness_threshold", 0.15),
        "min_width": data.get("min_width", 10.0),
        "min_height": data.get("min_height", 5.0),
        "word_overlap_threshold": data.get("word_overlap_threshold", 0.5),
    }


def _probe_worker() -> None:
    ENGINE.probe()


def _audit_pdf(input_handle: str, detection_rules=None, progress_id=None) -> tuple[int, dict, str, dict]:
    started = time.monotonic()
    analysis = _run_engine_operation("audit", progress_id, lambda op: ENGINE.audit(input_handle, detection_rules=detection_rules, operation_id=op))
    return analysis.pages, analysis.stats, analysis.analysis_id, {**(analysis.integrity or {}), "analysis_seconds": round(time.monotonic() - started, 3)}


def _render_preview_worker(meta: dict, page_number: int, kind: str, remove_mode: str, *, rotate=0, search_query="", highlight_findings=False, focus_box=None, progress_id=None):
    return _run_engine_operation("preview", progress_id, lambda op: ENGINE.preview(
        analysis_id=meta["analysis_id"],
        page=page_number,
        kind=kind,
        remove=remove_mode,
        rotate=rotate,
        search_query=search_query,
        highlight_findings=highlight_findings,
        focus_box=focus_box,
        operation_id=op,
    ))


def _export_pdf_worker(
    meta: dict, output: str, mode: str, remove: str, sanitize_active_content: bool, *, only_pages_with_findings=False, progress_id=None
) -> dict:
    output_handle = ENGINE.register_output(output)
    try:
        return _run_engine_operation("export", progress_id, lambda op: ENGINE.export(
            input_handle=meta["input_handle"],
            output_handle=output_handle,
            mode=mode,
            remove=remove,
            sanitize_active_content=sanitize_active_content,
            analysis_id=meta.get("analysis_id"),
            only_pages_with_findings=only_pages_with_findings,
            operation_id=op,
        ))
    finally:
        ENGINE.close_output(output_handle)

def _workspace_reserve(bytes_needed: int, *, reserve_job: bool = False) -> None:
    global WORKSPACE_RESERVED_BYTES, WORKSPACE_RESERVED_JOBS
    requested = max(0, int(bytes_needed))
    _cleanup_old_jobs()
    with WORKSPACE_LOCK, JOBS_LOCK:
        if reserve_job and len(JOBS) + WORKSPACE_RESERVED_JOBS >= MAX_ACTIVE_JOBS:
            raise RuntimeError("too many active PDF jobs")
        used = directory_size(WORK_ROOT)
        if used + WORKSPACE_RESERVED_BYTES + requested > MAX_WORKSPACE_BYTES:
            raise RuntimeError("PDF workspace quota exceeded")
        ensure_free_space(WORK_ROOT, requested, min_free_bytes=ENGINE_LIMITS.min_free_bytes)
        WORKSPACE_RESERVED_BYTES += requested
        if reserve_job:
            WORKSPACE_RESERVED_JOBS += 1


def _workspace_release(bytes_reserved: int, *, reserved_job: bool = False) -> None:
    global WORKSPACE_RESERVED_BYTES, WORKSPACE_RESERVED_JOBS
    with WORKSPACE_LOCK:
        WORKSPACE_RESERVED_BYTES = max(0, WORKSPACE_RESERVED_BYTES - max(0, int(bytes_reserved)))
        if reserved_job:
            WORKSPACE_RESERVED_JOBS = max(0, WORKSPACE_RESERVED_JOBS - 1)


def _export_reservation(meta: dict) -> int:
    input_size = int(meta.get("input_size", 0))
    return min(ENGINE_LIMITS.max_output_bytes, max(64 * 1024 * 1024, input_size * 3))

def _same_host_value(host: str, port: int) -> bool:
    host = (host or "").strip().lower()
    return host in {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}


def _valid_origin(origin: str, port: int) -> bool:
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"} and (parsed.port or 80) == port
    except ValueError:
        return False


def _generic_error(lang: str) -> str:
    return _message("processing_failed", lang)


class Handler(BaseHTTPRequestHandler):
    server_version = f"pdf-unredact/{__version__}"
    sys_version = ""

    def log_message(self, fmt, *args):
        LOG.info("[web] " + (fmt % args))

    def setup(self):
        super().setup()
        try:
            self.connection.settimeout(60)
        except OSError:
            pass

    def _lang(self, parsed=None):
        header = (self.headers.get("X-Language", "") or "").lower()
        if header.startswith("it"): return "it"
        if header.startswith("en"): return "en"
        if parsed is not None:
            query_lang = (urllib.parse.parse_qs(parsed.query).get("lang", [""])[0] or "").lower()
            if query_lang in {"it", "en"}: return query_lang
        return normalize_language((self.headers.get("Accept-Language", "") or "").lower())

    def _port(self) -> int:
        return int(self.server.server_address[1])

    def _session_cookie_ok(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get(COOKIE_NAME)
            supplied = morsel.value if morsel else ""
        except Exception:
            supplied = ""
        return bool(supplied and hmac.compare_digest(supplied, SESSION_TOKEN))

    def _request_allowed(self, require_session=False, require_origin=False) -> bool:
        if not _same_host_value(self.headers.get("Host", ""), self._port()):
            self._send(421, b"Misdirected Request"); return False
        if require_origin and not _valid_origin(self.headers.get("Origin", ""), self._port()):
            self._send(403, b"Forbidden"); return False
        if require_session and not self._session_cookie_ok():
            self._send(403, b"Forbidden"); return False
        return True

    def _security_headers(self):
        return {
            "Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Cross-Origin-Resource-Policy": "same-origin", "Cross-Origin-Opener-Policy": "same-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=()",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        }

    def _send(self, status, body=b"", content_type="text/plain; charset=utf-8", headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in self._security_headers().items():
                self.send_header(k, v)
            if headers:
                for k, v in headers.items():
                    self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Browsers routinely abort superseded preview/download requests.
            # Treat this as a normal disconnect, not an application failure.
            return

    def _send_file(self, status, path: Path, content_type: str, headers=None, delete_after=False):
        if not path.is_file():
            self._json(404, {"error": _message("file_unavailable", self._lang())})
            return
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(path.stat().st_size))
            for k, v in self._security_headers().items():
                self.send_header(k, v)
            if headers:
                for k, v in headers.items():
                    self.send_header(k, v)
            self.end_headers()
            with path.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A cancelled navigation/download closes the client socket while
            # the local server may still be producing or streaming the reply.
            return
        finally:
            if delete_after:
                path.unlink(missing_ok=True)

    def _json(self, status, obj):
        self._send(status, _json_bytes(obj), "application/json; charset=utf-8")

    def _safe_failure(self, exc, lang, prefix_key=None):
        LOG.error("request failed: %s", exc, exc_info=DEBUG)
        message = _generic_error(lang)
        if prefix_key: message = f"{_message(prefix_key, lang)}: {message}"
        self._json(400, {"error": message})

    def _read_json_body(self):
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError: raise ValueError("invalid content length")
        if length <= 0 or length > MAX_JSON_BYTES: raise ValueError("invalid JSON body size")
        obj = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(obj, dict): raise ValueError("JSON object required")
        return obj

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path); path = parsed.path
        if path == "/":
            if not self._request_allowed(): return
            supplied = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            if not self._session_cookie_ok() and not (supplied and hmac.compare_digest(supplied, SESSION_TOKEN)):
                self._send(403, b"Forbidden"); return
            initial_lang = normalize_language(self.headers.get("Accept-Language"))
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__APP_VERSION__", __version__).replace("__APP_REPOSITORY__", APP_REPOSITORY).replace("__HTML_LANG__", initial_lang)
            self._send(200, html.encode(), "text/html; charset=utf-8", {"Set-Cookie": f"{COOKIE_NAME}={SESSION_TOKEN}; Path=/; HttpOnly; SameSite=Strict"}); return
        if path == "/favicon.ico":
            if not self._request_allowed(): return
            self._send(200, FAVICON_SVG, "image/svg+xml; charset=utf-8"); return
        if path in {"/static/app.css", "/static/app.js"}:
            if not self._request_allowed(): return
            target = STATIC_DIR / path.rsplit("/", 1)[-1]
            self._send_file(200, target, "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8"); return
        if path.startswith("/api/") and not self._request_allowed(require_session=True): return
        if path.startswith("/api/i18n/"):
            requested = path.rsplit("/", 1)[-1].lower()
            if requested not in {"it", "en"}: self._json(404, {"error": _message("not_found", self._lang(parsed))}); return
            self._json(200, frontend_catalog(requested)); return
        if path.startswith("/api/progress/"):
            progress_id = _progress_id(path.rsplit("/", 1)[-1])
            if not progress_id: self._json(404, {"error": _message("not_found", self._lang(parsed))}); return
            with PROGRESS_LOCK: payload = dict(PROGRESS.get(progress_id, {"phase": "waiting"}))
            self._json(200, payload); return
        if path.startswith("/api/diagnostics/"):
            parts = path.strip("/").split("/"); meta = _job(parts[-1]) if len(parts) == 3 else None
            if not meta: self._json(404, {"error": _message("job_not_found", self._lang(parsed))}); return
            try:
                probe = ENGINE.probe()
                self._json(200, {"engine": ENGINE.api_info(), "workspace": ENGINE.workspace_status(), "probe": probe, "pages": meta["pages"], "integrity": meta.get("integrity", {}), "timings": meta.get("timings", {})})
            except Exception as exc: self._safe_failure(exc, self._lang(parsed))
            return
        if path.startswith("/api/recovered-text/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4: self._json(404, {"error": _message("not_found", self._lang(parsed))}); return
            _, _, job_id, fmt = parts; meta = _job(job_id)
            if not meta or fmt not in {"txt", "json", "csv"}: self._json(404, {"error": _message("job_not_found", self._lang(parsed))}); return
            try:
                data = ENGINE.extract_text(meta["analysis_id"]); entries = data.get("entries", [])
                stem = Path(meta["filename"]).stem + "_recovered"
                if fmt == "json":
                    body = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8"); content_type="application/json; charset=utf-8"
                elif fmt == "csv":
                    import csv, io
                    out = io.StringIO(); writer = csv.writer(out); writer.writerow(["page","source","x0","y0","x1","y1","text"])
                    for item in entries: writer.writerow([item["page"], item["source"], *item["box"], item["text"]])
                    body = out.getvalue().encode("utf-8"); content_type="text/csv; charset=utf-8"
                else:
                    body = ("\n".join(f'[Page {item["page"]}] {item["text"]}' for item in entries) + ("\n" if entries else "")).encode("utf-8"); content_type="text/plain; charset=utf-8"
                self._send(200, body, content_type, {"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'}); return
            except Exception as exc: self._safe_failure(exc, self._lang(parsed)); return
        if path.startswith("/api/preview/"):
            parts = path.strip("/").split("/")
            if len(parts) != 5: self._json(404, {"error": _message("invalid_endpoint", self._lang(parsed))}); return
            _, _, job_id, kind, page_text = parts; meta = _job(job_id)
            if not meta or kind not in {"original", "clean"}: self._json(404, {"error": _message("job_not_found", self._lang(parsed))}); return
            try:
                page_no = int(page_text)
                if not 1 <= page_no <= meta["pages"]: raise ValueError("invalid page")
                query = urllib.parse.parse_qs(parsed.query)
                remove_mode = query.get("remove", ["redactions"])[0]
                if remove_mode not in {"redactions", "all-annotations"}: remove_mode = "redactions"
                rotate = int(query.get("rotate", ["0"])[0]); rotate = rotate if rotate in {0,90,180,270} else 0
                search_query = query.get("search", [""])[0][:256]
                highlight_findings = query.get("highlight", ["0"])[0] == "1"
                focus_box = None
                if query.get("focus"):
                    try:
                        values = [float(v) for v in query["focus"][0].split(",")]; focus_box = values if len(values)==4 else None
                    except ValueError: pass
                progress_id = query.get("progress", [None])[0]
                with meta["lock"]:
                    started = time.monotonic()
                    artifact = _render_preview_worker(meta, page_no, kind, remove_mode, rotate=rotate, search_query=search_query, highlight_findings=highlight_findings, focus_box=focus_box, progress_id=progress_id)
                    meta.setdefault("timings", {})["preview_seconds"] = round(time.monotonic() - started, 3)
                    try:
                        body = ENGINE.read_artifact(artifact.artifact_handle)
                        self._send(200, body, artifact.mime)
                    finally:
                        ENGINE.release(artifact.artifact_handle)
            except Exception as exc: self._safe_failure(exc, self._lang(parsed))
            return
        if path.startswith("/api/report/"):
            parts = path.strip("/").split("/"); meta = _job(parts[-1]) if len(parts) == 3 else None
            if not meta or not meta.get("stats"): self._json(404, {"error": _message("report_unavailable", self._lang(parsed))}); return
            with meta["lock"]:
                data = json.dumps(meta["stats"], ensure_ascii=False, indent=2).encode()
            filename = Path(meta["filename"]).stem + "_audit.json"
            self._send(200, data, "application/json; charset=utf-8", {"Content-Disposition": f'attachment; filename="{filename}"'}); return
        if path.startswith("/api/download/"):
            parts = path.strip("/").split("/"); meta = _job(parts[-1]) if len(parts) == 3 else None
            if not meta or not meta.get("last_output"): self._json(404, {"error": _message("file_unavailable", self._lang(parsed))}); return
            with meta["lock"]:
                output = Path(meta["last_output"])
                if not _is_within(output, meta["dir"]) or not output.is_file(): self._json(404, {"error": _message("file_unavailable", self._lang(parsed))}); return
                self._send_file(200, output, "application/pdf", {"Content-Disposition": f'attachment; filename="{output.name}"'}, delete_after=True)
                meta["last_output"] = None
            return
        self._json(404, {"error": _message("not_found", self._lang(parsed))})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._request_allowed(require_session=True, require_origin=True): return
        if parsed.path.startswith("/api/search/"):
            parts = parsed.path.strip("/").split("/"); meta = _job(parts[-1]) if len(parts) == 3 else None
            if not meta: self._json(404, {"error": _message("job_not_found", self._lang(parsed))}); return
            try:
                data = self._read_json_body(); query = str(data.get("query", "")).strip()
                result = ENGINE.search(meta["analysis_id"], query)
                self._json(200, result)
            except Exception as exc: self._safe_failure(exc, self._lang(parsed))
            return
        if parsed.path == "/api/upload":
            lang = self._lang(parsed)
            try: length = int(self.headers.get("Content-Length", "0"))
            except ValueError: length = 0
            if length <= 0: self._json(400, {"error": _message("empty_file", lang)}); return
            if length > MAX_UPLOAD_BYTES: self._json(413, {"error": _message("too_large", lang).format(limit_mb=MAX_UPLOAD_BYTES // 1024 // 1024)}); return
            filename = _safe_filename(urllib.parse.unquote(self.headers.get("X-Filename", "document.pdf"))); job_id = uuid.uuid4().hex
            reserved = 0
            analysis_id = None
            input_handle = None
            try:
                _workspace_reserve(length, reserve_job=True); reserved = length
                job_dir = _secure_job_dir(job_id); input_path = job_dir / "input.pdf"; remaining = length; first = b""
                with input_path.open("xb") as f:
                    try: os.chmod(input_path, 0o600)
                    except OSError: pass
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk: raise ValueError("incomplete upload")
                        if len(first) < 5: first += chunk[:5-len(first)]
                        f.write(chunk); remaining -= len(chunk)
                if first != b"%PDF-": raise ValueError("invalid PDF signature")
                input_handle = ENGINE.register_input(input_path)
                input_path.unlink(missing_ok=True)
                progress_id = self.headers.get("X-Progress-Id")
                pages, stats, analysis_id, integrity = _audit_pdf(input_handle, progress_id=progress_id)
                meta = {"dir": str(job_dir), "input_handle": input_handle, "input_size": length, "filename": filename, "pages": pages, "created": time.time(), "last_output": None, "stats": stats, "analysis_id": analysis_id, "integrity": integrity, "timings": {"analysis_seconds": integrity.get("analysis_seconds", 0)}, "lock": threading.RLock()}
                with JOBS_LOCK: JOBS[job_id] = meta
                _workspace_release(reserved, reserved_job=True); reserved = 0
                self._json(200, {"job_id": job_id, "filename": filename, "pages": pages, "stats": stats, "integrity": integrity})
            except Exception as exc:
                _workspace_release(reserved, reserved_job=True)
                ENGINE.close_analysis(analysis_id)
                ENGINE.close_input(input_handle)
                shutil.rmtree(WORK_ROOT / job_id, ignore_errors=True); self._safe_failure(exc, lang, "cannot_analyze")
            return
        if parsed.path.startswith("/api/audit/"):
            parts = parsed.path.strip("/").split("/"); meta = _job(parts[-1]) if len(parts) == 3 else None
            if not meta: self._json(404, {"error": _message("job_not_found", self._lang(parsed))}); return
            try:
                with meta["lock"]:
                    old_analysis_id = meta.get("analysis_id")
                    data = self._read_json_body()
                    rules = _detection_rules_from(data.get("detection_rules"))
                    pages, stats, analysis_id, integrity = _audit_pdf(meta["input_handle"], rules, data.get("progress_id"))
                    if pages != meta["pages"]:
                        ENGINE.close_analysis(analysis_id)
                        raise RuntimeError("page count changed")
                    meta["stats"] = stats
                    meta["analysis_id"] = analysis_id
                    meta["integrity"] = integrity
                    meta.setdefault("timings", {})["analysis_seconds"] = integrity.get("analysis_seconds", 0)
                    ENGINE.close_analysis(old_analysis_id)
                self._json(200, {"stats": stats, "integrity": integrity})
            except Exception as exc: self._safe_failure(exc, self._lang(parsed))
            return
        if parsed.path == "/api/export":
            lang = self._lang(parsed)
            try:
                data = self._read_json_body(); job_id = str(data.get("job_id", "")); mode = data.get("mode", "clean"); remove = data.get("remove", "redactions")
                sanitize_active_content = data.get("sanitize_active_content", True)
                only_pages_with_findings = bool(data.get("only_pages_with_findings", False))
                progress_id = data.get("progress_id")
                if mode not in {"clean", "side_by_side"}: raise ValueError("invalid mode")
                if remove not in {"redactions", "all-annotations"}: raise ValueError("invalid annotation option")
                if not isinstance(sanitize_active_content, bool): raise ValueError("invalid sanitization option")
                meta = _job(job_id)
                if not meta: self._json(404, {"error": _message("job_not_found", lang)}); return
                stem = Path(meta["filename"]).stem; requested_name = str(data.get("filename", "")).strip()
                output_name = _safe_filename(requested_name) if requested_name else stem + ("_clean.pdf" if mode == "clean" else "_side_by_side.pdf")
                output = Path(meta["dir"]) / output_name
                if not _is_within(output, meta["dir"]): raise ValueError("invalid output path")
                export_reserve = _export_reservation(meta)
                _workspace_reserve(export_reserve)
                try:
                    with meta["lock"]:
                        output.unlink(missing_ok=True)
                        started=time.monotonic(); result = _export_pdf_worker(meta, str(output), mode, remove, sanitize_active_content, only_pages_with_findings=only_pages_with_findings, progress_id=progress_id); meta.setdefault("timings", {})["export_seconds"] = round(time.monotonic()-started,3)
                        if not output.is_file(): raise RuntimeError("output file missing")
                        meta["last_output"] = str(output)
                finally:
                    _workspace_release(export_reserve)
                self._json(200, {"filename": output.name, "download_url": f"/api/download/{job_id}", "clean_result": result})
            except Exception as exc: self._safe_failure(exc, lang)
            return
        self._json(404, {"error": _message("not_found", self._lang(parsed))})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._request_allowed(require_session=True, require_origin=True): return
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["api", "job"]:
            self._json(404, {"error": _message("not_found", self._lang(parsed))}); return
        job_id = parts[-1]
        with JOBS_LOCK:
            meta = JOBS.get(job_id)
        if not meta:
            self._send(204); return
        lock = meta["lock"]
        with lock:
            with JOBS_LOCK:
                removed = JOBS.pop(job_id, None)
            if removed:
                ENGINE.close_analysis(removed.get("analysis_id"))
                ENGINE.close_input(removed.get("input_handle"))
                shutil.rmtree(removed["dir"], ignore_errors=True)
        self._send(204)


class SecureThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16


def _create_server(preferred_port=8765):
    try: return SecureThreadingHTTPServer((HOST, preferred_port), Handler), preferred_port
    except OSError as exc:
        import errno
        if exc.errno != errno.EADDRINUSE: raise
    server = SecureThreadingHTTPServer((HOST, 0), Handler); return server, int(server.server_address[1])

def _dedupe_browser_candidates(candidates: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for candidate in candidates:
        key = (candidate["family"], os.path.normcase(candidate["path"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _sandbox_browser_candidates() -> list[dict]:
    """Return browsers that can provide a dedicated isolated maximized session.

    Chromium-family browsers are launched with an ephemeral ``--user-data-dir``.
    Firefox-family browsers are launched as a new instance with an ephemeral
    ``-profile`` directory. Safari is intentionally not returned: current Safari
    releases do not expose a supported command-line mechanism for a throw-away
    isolated profile suitable for this launcher.
    """
    candidates: list[dict] = []

    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        chromium_relpaths = [
            ("Chrome", r"Google\Chrome\Application\chrome.exe"),
            ("Edge", r"Microsoft\Edge\Application\msedge.exe"),
            ("Chromium", r"Chromium\Application\chrome.exe"),
            ("Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            ("Vivaldi", r"Vivaldi\Application\vivaldi.exe"),
        ]
        firefox_relpaths = [
            ("Firefox", r"Mozilla Firefox\firefox.exe"),
            ("LibreWolf", r"LibreWolf\librewolf.exe"),
            ("Waterfox", r"Waterfox\waterfox.exe"),
            ("Floorp", r"Ablaze Floorp\floorp.exe"),
            ("Zen", r"Zen Browser\zen.exe"),
        ]
        for root in roots:
            if not root:
                continue
            for name, rel in chromium_relpaths:
                path = os.path.join(root, rel)
                if os.path.isfile(path):
                    candidates.append({"family": "chromium", "path": path, "name": name})
            for name, rel in firefox_relpaths:
                path = os.path.join(root, rel)
                if os.path.isfile(path):
                    candidates.append({"family": "firefox", "path": path, "name": name})
        return _dedupe_browser_candidates(candidates)

    if sys.platform == "darwin":
        chromium_apps = [
            ("Chrome", "Google Chrome.app", "Google Chrome"),
            ("Edge", "Microsoft Edge.app", "Microsoft Edge"),
            ("Chromium", "Chromium.app", "Chromium"),
            ("Brave", "Brave Browser.app", "Brave Browser"),
            ("Vivaldi", "Vivaldi.app", "Vivaldi"),
            ("Opera", "Opera.app", "Opera"),
        ]
        firefox_apps = [
            ("Firefox", "Firefox.app", "firefox"),
            ("Firefox Developer Edition", "Firefox Developer Edition.app", "firefox"),
            ("LibreWolf", "LibreWolf.app", "librewolf"),
            ("Waterfox", "Waterfox.app", "waterfox"),
            ("Floorp", "Floorp.app", "floorp"),
            ("Zen", "Zen.app", "zen"),
        ]
        app_roots = ["/Applications", os.path.expanduser("~/Applications")]
        for app_root in app_roots:
            for name, app, binary in chromium_apps:
                path = os.path.join(app_root, app, "Contents", "MacOS", binary)
                if os.path.isfile(path):
                    candidates.append({"family": "chromium", "path": path, "name": name})
            for name, app, binary in firefox_apps:
                path = os.path.join(app_root, app, "Contents", "MacOS", binary)
                if os.path.isfile(path):
                    candidates.append({"family": "firefox", "path": path, "name": name})
        return _dedupe_browser_candidates(candidates)

    if sys.platform.startswith("linux"):
        chromium_names = [
            ("Chrome", "google-chrome"),
            ("Chrome", "google-chrome-stable"),
            ("Chromium", "chromium"),
            ("Chromium", "chromium-browser"),
            ("Edge", "microsoft-edge"),
            ("Edge", "microsoft-edge-stable"),
            ("Brave", "brave-browser"),
            ("Vivaldi", "vivaldi"),
            ("Vivaldi", "vivaldi-stable"),
            ("Opera", "opera"),
        ]
        firefox_names = [
            ("Firefox", "firefox"),
            ("Firefox ESR", "firefox-esr"),
            ("LibreWolf", "librewolf"),
            ("Waterfox", "waterfox"),
            ("Floorp", "floorp"),
            ("Zen", "zen-browser"),
            ("Zen", "zen"),
        ]
        trusted_dirs = [Path("/usr/bin"), Path("/usr/local/bin"), Path("/snap/bin"), Path("/opt/bin")]
        def trusted_executable(executable: str) -> str | None:
            for root in trusted_dirs:
                candidate = root / executable
                try:
                    if candidate.is_file() and os.access(candidate, os.X_OK):
                        resolved = candidate.resolve()
                        if any(_is_within(resolved, allowed) for allowed in [Path("/usr"), Path("/snap"), Path("/opt")]):
                            return str(resolved)
                except OSError:
                    continue
            return None
        for name, executable in chromium_names:
            path = trusted_executable(executable)
            if path:
                candidates.append({"family": "chromium", "path": path, "name": name})
        for name, executable in firefox_names:
            path = trusted_executable(executable)
            if path:
                candidates.append({"family": "firefox", "path": path, "name": name})

    return _dedupe_browser_candidates(candidates)


def _cleanup_browser_profile(process: subprocess.Popen, profile_dir: str) -> None:
    try:
        process.wait()
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
        with BROWSER_PROFILES_LOCK:
            BROWSER_PROFILES.discard(profile_dir)


def _screen_size() -> tuple[int, int] | None:
    """Best-effort primary-screen dimensions without adding a runtime dependency."""
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        width = int(root.winfo_screenwidth())
        height = int(root.winfo_screenheight())
        root.destroy()
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    return None


def _browser_launch_args(candidate: dict, url: str, profile_dir: str) -> list[str]:
    family = candidate["family"]
    executable = candidate["path"]

    if family == "chromium":
        return [
            executable,
            f"--app={url}",
            "--start-maximized",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-translate",
            "--disable-features=Translate,TranslateUI",
        ]

    if family == "firefox":
        # -profile / -no-remote keep the session separate from the user's
        # normal profile and any already-running Firefox instance. We deliberately
        # avoid kiosk mode: this must remain a normal resizable window with title
        # bar and window controls. Firefox has no portable start-maximized switch,
        # so request the current screen dimensions as a best-effort equivalent.
        args = [
            executable,
            "-no-remote",
            "-new-instance",
            "-profile",
            profile_dir,
        ]
        screen_size = _screen_size()
        if screen_size:
            width, height = screen_size
            args.extend(["--width", str(width), "--height", str(height)])
        args.extend(["-private-window", url])
        return args

    raise ValueError(f"Unsupported browser family: {family}")


def _open_sandbox_browser(url: str) -> str | None:
    """Open the UI in an isolated dedicated browser window.

    The operating-system default browser and normal user profiles are never used.
    Chromium-family browsers use a temporary user-data directory and request a
    maximized app window. Firefox-family browsers use a temporary profile and a
    normal private window (never kiosk / true full-screen). Browser security
    sandboxes remain enabled.

    Returns the launched browser display name, or ``None`` if no compatible
    browser could be started.
    """
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        LOG.warning("Automatic browser launch disabled while running as root")
        return None
    for candidate in _sandbox_browser_candidates():
        profile_dir = tempfile.mkdtemp(prefix="pdf-unredact-browser-")
        try:
            Path(profile_dir, TEMP_MARKER).write_text("pdf-unredact-browser\n", encoding="ascii")
        except OSError:
            pass
        try:
            os.chmod(profile_dir, 0o700)
        except OSError:
            pass
        with BROWSER_PROFILES_LOCK:
            BROWSER_PROFILES.add(profile_dir)
        args = _browser_launch_args(candidate, url, profile_dir)
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=(os.name != "nt"),
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                    if os.name == "nt"
                    else 0
                ),
            )
        except (OSError, subprocess.SubprocessError):
            shutil.rmtree(profile_dir, ignore_errors=True)
            with BROWSER_PROFILES_LOCK:
                BROWSER_PROFILES.discard(profile_dir)
            continue

        threading.Thread(
            target=_cleanup_browser_profile,
            args=(process, profile_dir),
            daemon=True,
        ).start()
        return candidate["name"]

    return None




def _install_signal_handlers(server) -> None:
    def stop(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try: signal.signal(sig, stop)
            except (ValueError, OSError): pass


def run_web(port=8765, open_browser=True):
    _cleanup_stale_temp("pdf-unredact-", older_than=24 * 60 * 60)
    _cleanup_stale_temp("pdf-unredact-browser-", older_than=24 * 60 * 60)
    # Fail before opening the browser if the isolated engine itself cannot start.
    # This avoids turning worker bootstrap problems into a generic upload error.
    _probe_worker()
    server, actual_port = _create_server(port); _install_signal_handlers(server)
    url = f"http://{HOST}:{actual_port}/?token={urllib.parse.quote(SESSION_TOKEN)}"; console_lang = _system_language(); print("pdf-unredact")
    if actual_port != port: print(translate("port_in_use", console_lang, port=port, actual=actual_port))
    print(translate("open_url", console_lang, url=url)); print(translate("press_stop", console_lang))
    if open_browser:
        def launch_ui():
            browser_name = _open_sandbox_browser(url)
            if browser_name: print(translate("sandbox_browser_started", console_lang, browser=browser_name))
            else: print(translate("sandbox_browser_unavailable", console_lang, url=url))
        threading.Timer(0.6, launch_ui).start()
    try: server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); _cleanup_all(); print(translate("stopped", console_lang))


if __name__ == "__main__":
    run_web()
