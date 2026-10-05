"""Frontend-neutral security boundary for the PDF engine.

The engine owns immutable staged inputs, temporary artifacts, and output
capabilities. Frontends (Tauri, Flutter, CLI, or the legacy local web host)
never choose worker temporary paths or authorization roots.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from worker_client import DEFAULT_MAX_RESULT_BYTES, WorkerCancelledError, WorkerError, WorkerMemoryError, run_worker

_MIB = 1024 * 1024

ENGINE_API_VERSION = 1


class EngineErrorCode:
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_PDF = "INVALID_PDF"
    PASSWORD_PROTECTED = "PASSWORD_PROTECTED"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    INPUT_CHANGED = "INPUT_CHANGED"
    OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    OUT_OF_DISK_SPACE = "OUT_OF_DISK_SPACE"
    WORKSPACE_QUOTA_EXCEEDED = "WORKSPACE_QUOTA_EXCEEDED"
    WORKER_TIMEOUT = "WORKER_TIMEOUT"
    WORKER_MEMORY_LIMIT = "WORKER_MEMORY_LIMIT"
    WORKER_CRASH = "WORKER_CRASH"
    CANCELLED = "CANCELLED"
    SANITIZATION_FAILED = "SANITIZATION_FAILED"
    EXPORT_FAILED = "EXPORT_FAILED"
    ENGINE_CLOSED = "ENGINE_CLOSED"
    HANDLE_UNAVAILABLE = "HANDLE_UNAVAILABLE"


class EngineError(RuntimeError):
    """Stable frontend-facing engine exception.

    ``code`` is part of the engine API contract; ``message`` is diagnostic and
    should not be parsed by frontends.
    """

    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "api_version": ENGINE_API_VERSION,
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProgressEvent:
    operation_id: str
    operation: str
    phase: str
    current: int | None = None
    total: int | None = None

    def to_dict(self) -> dict:
        return {
            "api_version": ENGINE_API_VERSION,
            "operation_id": self.operation_id,
            "operation": self.operation,
            "phase": self.phase,
            "current": self.current,
            "total": self.total,
        }



def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


@dataclass(frozen=True)
class EngineLimits:
    max_input_bytes: int = 250 * _MIB
    max_output_bytes: int = 1024 * _MIB
    max_pages: int = 2000
    max_preview_pixels: int = 25_000_000
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES
    worker_max_rss_mb: int = 1536
    audit_timeout: int = 120
    preview_timeout: int = 30
    export_timeout: int = 240
    min_free_bytes: int = 512 * _MIB
    max_workspace_bytes: int = 2048 * _MIB

    @classmethod
    def from_env(cls) -> "EngineLimits":
        return cls(
            max_input_bytes=_env_int("PDF_UNREDACT_MAX_UPLOAD_MB", 250, minimum=1) * _MIB,
            max_output_bytes=_env_int("PDF_UNREDACT_MAX_OUTPUT_MB", 1024, minimum=1) * _MIB,
            max_pages=_env_int("PDF_UNREDACT_MAX_PAGES", 2000, minimum=1),
            max_preview_pixels=_env_int("PDF_UNREDACT_MAX_PREVIEW_PIXELS", 25_000_000, minimum=1),
            max_result_bytes=_env_int("PDF_UNREDACT_MAX_RESULT_MB", 32, minimum=1) * _MIB,
            worker_max_rss_mb=_env_int("PDF_UNREDACT_WORKER_MAX_RSS_MB", 1536),
            audit_timeout=_env_int("PDF_UNREDACT_AUDIT_TIMEOUT", 120, minimum=1),
            preview_timeout=_env_int("PDF_UNREDACT_PREVIEW_TIMEOUT", 30, minimum=1),
            export_timeout=_env_int("PDF_UNREDACT_EXPORT_TIMEOUT", 240, minimum=1),
            min_free_bytes=_env_int("PDF_UNREDACT_MIN_FREE_MB", 512) * _MIB,
            max_workspace_bytes=_env_int("PDF_UNREDACT_MAX_WORKSPACE_MB", 2048, minimum=1) * _MIB,
        )


@dataclass(frozen=True)
class AnalysisResult:
    analysis_id: str
    pages: int
    stats: dict

    def to_dict(self) -> dict:
        return {
            "api_version": ENGINE_API_VERSION,
            "analysis_id": self.analysis_id,
            "pages": self.pages,
            "stats": self.stats,
        }


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def directory_size(root: str | Path) -> int:
    """Best-effort byte count that never follows directory symlinks."""
    root_path = Path(root)
    total = 0
    try:
        for base, dirs, files in os.walk(root_path, followlinks=False):
            base_path = Path(base)
            dirs[:] = [d for d in dirs if not (base_path / d).is_symlink()]
            for name in files:
                path = base_path / name
                try:
                    if not path.is_symlink():
                        total += path.stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def ensure_free_space(path: str | Path, required_bytes: int, *, min_free_bytes: int = 0) -> None:
    anchor = Path(path)
    while not anchor.exists() and anchor.parent != anchor:
        anchor = anchor.parent
    free = shutil.disk_usage(anchor).free
    required = max(0, int(required_bytes)) + max(0, int(min_free_bytes))
    if free < required:
        raise OSError("insufficient free disk space for PDF processing")


class EngineClient:
    """Capability-based API for the short-lived PDF worker.

    User paths are registered once and converted to opaque handles. Inputs are
    copied into an engine-owned private workspace before any worker sees them.
    Exports are staged into a random temporary file in the destination
    filesystem and committed with ``os.replace`` only after successful worker
    completion and validation.
    """

    def __init__(
        self,
        *,
        limits: EngineLimits | None = None,
        concurrency_guard=None,
        log_debug: Callable[[str], None] | None = None,
        log_error: Callable[[str], None] | None = None,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        self.limits = limits or EngineLimits.from_env()
        self.concurrency_guard = concurrency_guard
        self.log_debug = log_debug
        self.log_error = log_error
        self.progress_callback = progress_callback
        self._lock = threading.RLock()
        self._closed = False
        self._inputs: dict[str, dict] = {}
        self._outputs: dict[str, Path] = {}
        self._analyses: dict[str, dict] = {}
        self._operations: dict[str, dict] = {}
        self._temp_root = Path(tempfile.mkdtemp(prefix="pdf-unredact-engine-"))
        self._input_root = self._temp_root / "inputs"
        self._input_root.mkdir(mode=0o700)
        try:
            os.chmod(self._temp_root, 0o700)
            os.chmod(self._input_root, 0o700)
        except OSError:
            pass
        atexit.register(self.close)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for op in self._operations.values():
                op["cancel"].set()
            self._operations.clear()
            self._analyses.clear()
            self._inputs.clear()
            self._outputs.clear()
            shutil.rmtree(self._temp_root, ignore_errors=True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise EngineError(EngineErrorCode.ENGINE_CLOSED, "PDF engine is closed")

    def _operation_dir(self, prefix: str) -> Path:
        self._ensure_open()
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=self._temp_root))
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        return path

    def create_operation(self, operation: str) -> str:
        """Create a cancellable operation handle for a future engine call."""
        self._ensure_open()
        if operation not in {"audit", "preview", "export", "probe"}:
            raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "invalid operation type")
        operation_id = uuid.uuid4().hex
        with self._lock:
            self._operations[operation_id] = {
                "operation": operation,
                "cancel": threading.Event(),
                "active": False,
            }
        return operation_id

    def cancel(self, operation_id: str) -> bool:
        """Request cancellation. Returns False when the handle is unknown."""
        with self._lock:
            op = self._operations.get(str(operation_id))
            if not op:
                return False
            op["cancel"].set()
        return True

    def close_operation(self, operation_id: str | None) -> None:
        if not operation_id:
            return
        with self._lock:
            self._operations.pop(str(operation_id), None)

    def release(self, handle: str | None) -> bool:
        """Release any engine-owned opaque handle.

        This is the preferred lifecycle primitive for desktop frontends. It is
        idempotent and does not require the caller to know the handle type.
        """
        if not handle:
            return False
        value = str(handle)
        with self._lock:
            if value in self._operations:
                self._operations[value]["cancel"].set()
                self._operations.pop(value, None)
                return True
            is_output = value in self._outputs
            is_input = value in self._inputs
            is_analysis = value in self._analyses
        if is_analysis:
            self.close_analysis(value)
            return True
        if is_output:
            self.close_output(value)
            return True
        if is_input:
            self.close_input(value)
            return True
        return False

    def _operation(self, operation: str, operation_id: str | None) -> tuple[str, threading.Event, bool]:
        owned = operation_id is None
        if owned:
            operation_id = self.create_operation(operation)
        with self._lock:
            item = self._operations.get(str(operation_id))
            if not item or item["operation"] != operation:
                raise EngineError(EngineErrorCode.HANDLE_UNAVAILABLE, "operation handle is unavailable")
            if item["active"]:
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "operation handle is already active")
            item["active"] = True
            cancel_event = item["cancel"]
        return str(operation_id), cancel_event, owned

    def _finish_operation(self, operation_id: str, owned: bool) -> None:
        with self._lock:
            item = self._operations.get(operation_id)
            if item:
                item["active"] = False
        if owned:
            self.close_operation(operation_id)

    def _emit_progress(
        self, operation_id: str, operation: str, phase: str,
        current: int | None = None, total: int | None = None,
    ) -> None:
        if not self.progress_callback:
            return
        event = ProgressEvent(operation_id, operation, phase, current, total)
        try:
            self.progress_callback(event)
        except Exception:
            pass

    def _raise_stable(self, exc: BaseException) -> None:
        if isinstance(exc, EngineError):
            raise exc
        text = str(exc)
        low = text.lower()
        if isinstance(exc, WorkerCancelledError):
            code = EngineErrorCode.CANCELLED
        elif isinstance(exc, TimeoutError):
            code = EngineErrorCode.WORKER_TIMEOUT
        elif isinstance(exc, WorkerMemoryError):
            code = EngineErrorCode.WORKER_MEMORY_LIMIT
        elif "password-protected" in low:
            code = EngineErrorCode.PASSWORD_PROTECTED
        elif "too many pages" in low:
            code = EngineErrorCode.TOO_MANY_PAGES
        elif "size limit" in low and "input" in low:
            code = EngineErrorCode.INPUT_TOO_LARGE
        elif "size limit" in low and ("output" in low or "exported" in low):
            code = EngineErrorCode.OUTPUT_TOO_LARGE
        elif "workspace quota" in low:
            code = EngineErrorCode.WORKSPACE_QUOTA_EXCEEDED
        elif "disk space" in low:
            code = EngineErrorCode.OUT_OF_DISK_SPACE
        elif "sanit" in low or "sanific" in low:
            code = EngineErrorCode.SANITIZATION_FAILED
        elif isinstance(exc, WorkerError):
            code = EngineErrorCode.WORKER_CRASH
        elif isinstance(exc, (ValueError, TypeError)):
            code = EngineErrorCode.INVALID_ARGUMENT
        else:
            code = EngineErrorCode.EXPORT_FAILED
        raise EngineError(code, text or code) from exc

    def _run(
        self, args: list[str], result_path: Path, timeout: int, *,
        operation_id: str | None = None, operation: str = "probe",
        cancel_event=None,
    ) -> dict:
        def worker_progress(payload: dict) -> None:
            if operation_id:
                self._emit_progress(operation_id, operation, payload.get("phase", "working"))
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise WorkerCancelledError("PDF worker operation was cancelled")
            return run_worker(
                args,
                result_path,
                timeout,
                max_result_bytes=self.limits.max_result_bytes,
                max_rss_mb=self.limits.worker_max_rss_mb,
                max_file_bytes=self.limits.max_output_bytes,
                concurrency_guard=self.concurrency_guard,
                log_debug=self.log_debug,
                log_error=self.log_error,
                cancel_event=cancel_event,
                progress_callback=worker_progress,
            )
        except BaseException as exc:
            self._raise_stable(exc)
            raise AssertionError("unreachable")

    def validate_input(self, input_path: str | Path) -> Path:
        """Validate a user-selected source before staging it."""
        path = _resolved(input_path)
        if not path.is_file() or path.is_symlink():
            raise EngineError(EngineErrorCode.INVALID_PDF, "input PDF does not exist or is not a regular file")
        size = path.stat().st_size
        if size <= 0 or size > self.limits.max_input_bytes:
            raise EngineError(EngineErrorCode.INPUT_TOO_LARGE, "input PDF exceeds the configured size limit")
        with path.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise EngineError(EngineErrorCode.INVALID_PDF, "input is not a PDF file")
        return path

    def api_info(self) -> dict:
        return {
            "api_version": ENGINE_API_VERSION,
            "operations": ["register_input", "audit", "preview", "register_output", "export", "cancel", "release"],
            "error_codes": [value for name, value in vars(EngineErrorCode).items() if name.isupper()],
        }

    def register_input(self, input_path: str | Path) -> str:
        """Copy a user-selected PDF into immutable engine-owned staging."""
        src = self.validate_input(input_path)
        size = src.stat().st_size
        if directory_size(self._temp_root) + size > self.limits.max_workspace_bytes:
            raise EngineError(EngineErrorCode.WORKSPACE_QUOTA_EXCEEDED, "PDF engine workspace quota exceeded")
        try:
            ensure_free_space(self._input_root, size, min_free_bytes=self.limits.min_free_bytes)
        except OSError as exc:
            raise EngineError(EngineErrorCode.OUT_OF_DISK_SPACE, str(exc)) from exc
        handle = uuid.uuid4().hex
        staged = self._input_root / f"{handle}.pdf"
        try:
            with src.open("rb") as reader, staged.open("xb") as writer:
                source_before = os.fstat(reader.fileno())
                try:
                    os.chmod(staged, 0o600)
                except OSError:
                    pass
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
                source_after = os.fstat(reader.fileno())
            stable_fields_before = (source_before.st_size, source_before.st_mtime_ns)
            stable_fields_after = (source_after.st_size, source_after.st_mtime_ns)
            if stable_fields_before != stable_fields_after or staged.stat().st_size != source_after.st_size:
                raise EngineError(EngineErrorCode.INPUT_CHANGED, "input PDF changed while being staged")
            with staged.open("rb") as fh:
                if fh.read(5) != b"%PDF-":
                    raise EngineError(EngineErrorCode.INVALID_PDF, "staged input is not a PDF file")
            try:
                os.chmod(staged, 0o400)
            except OSError:
                pass
            with self._lock:
                self._inputs[handle] = {"path": staged, "size": size}
            return handle
        except BaseException as exc:
            staged.unlink(missing_ok=True)
            if isinstance(exc, EngineError):
                raise
            self._raise_stable(exc)
            raise AssertionError("unreachable")

    def close_input(self, input_handle: str | None) -> None:
        if not input_handle:
            return
        with self._lock:
            item = self._inputs.pop(str(input_handle), None)
            doomed = [aid for aid, a in self._analyses.items() if a.get("input_handle") == input_handle]
        for analysis_id in doomed:
            self.close_analysis(analysis_id)
        if item:
            try:
                os.chmod(item["path"], 0o600)
            except OSError:
                pass
            item["path"].unlink(missing_ok=True)

    def register_output(self, output_path: str | Path) -> str:
        """Authorize exactly one concrete user-selected destination path."""
        self._ensure_open()
        raw = Path(output_path).expanduser()
        parent = raw.parent.resolve()
        if not parent.is_dir():
            raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "output directory does not exist")
        output = (parent / raw.name).resolve()
        if output.exists() and (not output.is_file() or output.is_symlink()):
            raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "output path is not a regular file destination")
        handle = uuid.uuid4().hex
        with self._lock:
            self._outputs[handle] = output
        return handle

    def close_output(self, output_handle: str | None) -> None:
        if not output_handle:
            return
        with self._lock:
            self._outputs.pop(str(output_handle), None)

    def _input_for(self, input_handle: str) -> Path:
        if not isinstance(input_handle, str) or len(input_handle) != 32:
            raise EngineError(EngineErrorCode.HANDLE_UNAVAILABLE, "invalid input handle")
        with self._lock:
            item = self._inputs.get(input_handle)
        if not item or not item["path"].is_file():
            raise EngineError(EngineErrorCode.HANDLE_UNAVAILABLE, "input handle is unavailable")
        return item["path"]

    def _output_for(self, output_handle: str) -> Path:
        if not isinstance(output_handle, str) or len(output_handle) != 32:
            raise EngineError(EngineErrorCode.HANDLE_UNAVAILABLE, "invalid output handle")
        with self._lock:
            output = self._outputs.get(output_handle)
        if not output:
            raise EngineError(EngineErrorCode.HANDLE_UNAVAILABLE, "output handle is unavailable")
        return output

    def probe(self, *, operation_id: str | None = None) -> dict:
        op_id, cancel_event, owned = self._operation("probe", operation_id)
        work = self._operation_dir("probe-")
        result = work / "result.json"
        try:
            self._emit_progress(op_id, "probe", "starting")
            data = self._run(["probe"], result, min(20, self.limits.preview_timeout), operation_id=op_id, operation="probe", cancel_event=cancel_event)
            self._emit_progress(op_id, "probe", "completed", 1, 1)
            return {"api_version": ENGINE_API_VERSION, **data}
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self._finish_operation(op_id, owned)

    def audit(self, input_handle: str, *, operation_id: str | None = None) -> AnalysisResult:
        op_id, cancel_event, owned = self._operation("audit", operation_id)
        work: Path | None = None
        try:
            self._emit_progress(op_id, "audit", "starting")
            src = self._input_for(input_handle)
            work = self._operation_dir("analysis-")
            result = work / "worker-result.json"
            data = self._run(
                [
                    "audit", "--input", str(src),
                    "--max-pages", str(self.limits.max_pages),
                    "--max-input-bytes", str(self.limits.max_input_bytes),
                ],
                result,
                self.limits.audit_timeout, operation_id=op_id, operation="audit", cancel_event=cancel_event,
            )
            stats = data["stats"]
            stats_path = work / "audit.json"
            stats_path.write_text(__import__("json").dumps(stats, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(stats_path, 0o600)
            except OSError:
                pass
            result.unlink(missing_ok=True)
            analysis_id = uuid.uuid4().hex
            with self._lock:
                self._analyses[analysis_id] = {
                    "dir": work,
                    "stats": stats_path,
                    "input_handle": input_handle,
                    "pages": int(data["pages"]),
                }
            self._emit_progress(op_id, "audit", "completed", int(data["pages"]), int(data["pages"]))
            return AnalysisResult(analysis_id=analysis_id, pages=int(data["pages"]), stats=stats)
        except BaseException as exc:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            if isinstance(exc, EngineError):
                raise
            self._raise_stable(exc)
            raise AssertionError("unreachable")
        finally:
            self._finish_operation(op_id, owned)

    def close_analysis(self, analysis_id: str | None) -> None:
        if not analysis_id:
            return
        with self._lock:
            item = self._analyses.pop(str(analysis_id), None)
        if item:
            shutil.rmtree(item["dir"], ignore_errors=True)

    def _analysis_for(self, analysis_id: str) -> dict:
        if not isinstance(analysis_id, str) or len(analysis_id) != 32:
            raise EngineError(EngineErrorCode.HANDLE_UNAVAILABLE, "invalid analysis handle")
        with self._lock:
            item = self._analyses.get(analysis_id)
        if not item:
            raise EngineError(EngineErrorCode.HANDLE_UNAVAILABLE, "analysis handle is unavailable")
        self._input_for(item["input_handle"])
        return item

    def preview(
        self,
        *,
        analysis_id: str,
        page: int,
        kind: str,
        remove: str,
        operation_id: str | None = None,
    ) -> Path:
        op_id, cancel_event, owned = self._operation("preview", operation_id)
        work: Path | None = None
        try:
            self._emit_progress(op_id, "preview", "starting")
            analysis = self._analysis_for(analysis_id)
            src = self._input_for(analysis["input_handle"])
            if page < 1 or page > analysis["pages"]:
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "invalid page")
            if kind not in {"original", "clean"}:
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "invalid preview kind")
            if remove not in {"redactions", "all-annotations"}:
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "invalid annotation removal mode")
            ensure_free_space(self._temp_root, 16 * _MIB, min_free_bytes=self.limits.min_free_bytes)
            output = Path(analysis["dir"]) / f"preview-{uuid.uuid4().hex}.png"
            work = self._operation_dir("preview-op-")
            result = work / "result.json"
            self._run(
                [
                    "preview", "--input", str(src), "--output", str(output),
                    "--stats", str(analysis["stats"]), "--page", str(page), "--kind", kind,
                    "--remove", remove, "--max-pixels", str(self.limits.max_preview_pixels),
                    "--max-pages", str(self.limits.max_pages),
                    "--max-input-bytes", str(self.limits.max_input_bytes),
                ],
                result,
                self.limits.preview_timeout, operation_id=op_id, operation="preview", cancel_event=cancel_event,
            )
            if not output.is_file():
                raise EngineError(EngineErrorCode.EXPORT_FAILED, "preview was not generated")
            self._emit_progress(op_id, "preview", "completed", page, analysis["pages"])
            return output
        except BaseException as exc:
            if isinstance(exc, EngineError):
                raise
            self._raise_stable(exc)
            raise AssertionError("unreachable")
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            self._finish_operation(op_id, owned)

    def export(
        self,
        *,
        input_handle: str,
        output_handle: str,
        mode: str,
        remove: str,
        sanitize_active_content: bool = True,
        operation_id: str | None = None,
    ) -> dict:
        op_id, cancel_event, owned = self._operation("export", operation_id)
        temp_output: Path | None = None
        work: Path | None = None
        try:
            self._emit_progress(op_id, "export", "starting")
            src = self._input_for(input_handle)
            output = self._output_for(output_handle)
            if mode not in {"clean", "side_by_side"}:
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "invalid export mode")
            if remove not in {"redactions", "all-annotations"}:
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "invalid annotation removal mode")
            if not isinstance(sanitize_active_content, bool):
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "sanitize_active_content must be a boolean")

            expected_growth = min(self.limits.max_output_bytes, max(64 * _MIB, src.stat().st_size * 3))
            ensure_free_space(output.parent, expected_growth, min_free_bytes=self.limits.min_free_bytes)

            fd, temp_name = tempfile.mkstemp(prefix=".pdf-unredact-", suffix=".tmp.pdf", dir=output.parent)
            os.close(fd)
            temp_output = Path(temp_name)
            temp_output.unlink(missing_ok=True)
            work = self._operation_dir("export-")
            result = work / "result.json"
            args = [
                "export", "--input", str(src), "--output", str(temp_output),
                "--mode", mode, "--remove", remove,
                "--max-pages", str(self.limits.max_pages),
                "--max-input-bytes", str(self.limits.max_input_bytes),
                "--max-output-bytes", str(self.limits.max_output_bytes),
            ]
            args.append("--sanitize-active-content" if sanitize_active_content else "--no-sanitize-active-content")
            data = self._run(args, result, self.limits.export_timeout, operation_id=op_id, operation="export", cancel_event=cancel_event)
            self._emit_progress(op_id, "export", "verifying")
            if not temp_output.is_file() or temp_output.stat().st_size > self.limits.max_output_bytes:
                raise EngineError(EngineErrorCode.OUTPUT_TOO_LARGE, "exported PDF exceeds the configured size limit")
            with temp_output.open("rb") as fh:
                os.fsync(fh.fileno())
            if output.exists() and (output.is_symlink() or not output.is_file()):
                raise EngineError(EngineErrorCode.INVALID_ARGUMENT, "output path is no longer a regular file destination")
            self._emit_progress(op_id, "export", "committing")
            os.replace(temp_output, output)
            self._emit_progress(op_id, "export", "completed", 1, 1)
            return {"api_version": ENGINE_API_VERSION, **data["clean_result"]}
        except BaseException as exc:
            if isinstance(exc, EngineError):
                raise
            self._raise_stable(exc)
            raise AssertionError("unreachable")
        finally:
            if temp_output is not None:
                temp_output.unlink(missing_ok=True)
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            self._finish_operation(op_id, owned)

