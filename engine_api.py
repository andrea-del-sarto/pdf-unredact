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

from worker_client import DEFAULT_MAX_RESULT_BYTES, run_worker

_MIB = 1024 * 1024


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
    ) -> None:
        self.limits = limits or EngineLimits.from_env()
        self.concurrency_guard = concurrency_guard
        self.log_debug = log_debug
        self.log_error = log_error
        self._lock = threading.RLock()
        self._closed = False
        self._inputs: dict[str, dict] = {}
        self._outputs: dict[str, Path] = {}
        self._analyses: dict[str, dict] = {}
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
            self._analyses.clear()
            self._inputs.clear()
            self._outputs.clear()
            shutil.rmtree(self._temp_root, ignore_errors=True)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("PDF engine is closed")

    def _operation_dir(self, prefix: str) -> Path:
        self._ensure_open()
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=self._temp_root))
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        return path

    def _run(self, args: list[str], result_path: Path, timeout: int) -> dict:
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
        )

    def validate_input(self, input_path: str | Path) -> Path:
        """Validate a user-selected source before staging it."""
        path = _resolved(input_path)
        if not path.is_file() or path.is_symlink():
            raise ValueError("input PDF does not exist or is not a regular file")
        size = path.stat().st_size
        if size <= 0 or size > self.limits.max_input_bytes:
            raise ValueError("input PDF exceeds the configured size limit")
        with path.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise ValueError("input is not a PDF file")
        return path

    def register_input(self, input_path: str | Path) -> str:
        """Copy a user-selected PDF into immutable engine-owned staging."""
        src = self.validate_input(input_path)
        size = src.stat().st_size
        if directory_size(self._temp_root) + size > self.limits.max_workspace_bytes:
            raise OSError("PDF engine workspace quota exceeded")
        ensure_free_space(self._input_root, size, min_free_bytes=self.limits.min_free_bytes)
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
                raise RuntimeError("input PDF changed while being staged")
            with staged.open("rb") as fh:
                if fh.read(5) != b"%PDF-":
                    raise ValueError("staged input is not a PDF file")
            try:
                os.chmod(staged, 0o400)
            except OSError:
                pass
            with self._lock:
                self._inputs[handle] = {"path": staged, "size": size}
            return handle
        except Exception:
            staged.unlink(missing_ok=True)
            raise

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
            raise ValueError("output directory does not exist")
        output = (parent / raw.name).resolve()
        if output.exists() and (not output.is_file() or output.is_symlink()):
            raise ValueError("output path is not a regular file destination")
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
            raise ValueError("invalid input handle")
        with self._lock:
            item = self._inputs.get(input_handle)
        if not item or not item["path"].is_file():
            raise ValueError("input handle is unavailable")
        return item["path"]

    def _output_for(self, output_handle: str) -> Path:
        if not isinstance(output_handle, str) or len(output_handle) != 32:
            raise ValueError("invalid output handle")
        with self._lock:
            output = self._outputs.get(output_handle)
        if not output:
            raise ValueError("output handle is unavailable")
        return output

    def probe(self) -> dict:
        work = self._operation_dir("probe-")
        result = work / "result.json"
        try:
            return self._run(["probe"], result, min(20, self.limits.preview_timeout))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def audit(self, input_handle: str) -> AnalysisResult:
        src = self._input_for(input_handle)
        work = self._operation_dir("analysis-")
        result = work / "worker-result.json"
        try:
            data = self._run(
                [
                    "audit", "--input", str(src),
                    "--max-pages", str(self.limits.max_pages),
                    "--max-input-bytes", str(self.limits.max_input_bytes),
                ],
                result,
                self.limits.audit_timeout,
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
            return AnalysisResult(analysis_id=analysis_id, pages=int(data["pages"]), stats=stats)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise

    def close_analysis(self, analysis_id: str | None) -> None:
        if not analysis_id:
            return
        with self._lock:
            item = self._analyses.pop(str(analysis_id), None)
        if item:
            shutil.rmtree(item["dir"], ignore_errors=True)

    def _analysis_for(self, analysis_id: str) -> dict:
        if not isinstance(analysis_id, str) or len(analysis_id) != 32:
            raise ValueError("invalid analysis handle")
        with self._lock:
            item = self._analyses.get(analysis_id)
        if not item:
            raise ValueError("analysis handle is unavailable")
        self._input_for(item["input_handle"])
        return item

    def preview(
        self,
        *,
        analysis_id: str,
        page: int,
        kind: str,
        remove: str,
    ) -> Path:
        analysis = self._analysis_for(analysis_id)
        src = self._input_for(analysis["input_handle"])
        if page < 1 or page > analysis["pages"]:
            raise ValueError("invalid page")
        if kind not in {"original", "clean"}:
            raise ValueError("invalid preview kind")
        if remove not in {"redactions", "all-annotations"}:
            raise ValueError("invalid annotation removal mode")
        ensure_free_space(self._temp_root, 16 * _MIB, min_free_bytes=self.limits.min_free_bytes)
        output = Path(analysis["dir"]) / f"preview-{uuid.uuid4().hex}.png"
        work = self._operation_dir("preview-op-")
        result = work / "result.json"
        try:
            self._run(
                [
                    "preview", "--input", str(src), "--output", str(output),
                    "--stats", str(analysis["stats"]), "--page", str(page), "--kind", kind,
                    "--remove", remove, "--max-pixels", str(self.limits.max_preview_pixels),
                    "--max-pages", str(self.limits.max_pages),
                    "--max-input-bytes", str(self.limits.max_input_bytes),
                ],
                result,
                self.limits.preview_timeout,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if not output.is_file():
            raise RuntimeError("preview was not generated")
        return output

    def export(
        self,
        *,
        input_handle: str,
        output_handle: str,
        mode: str,
        remove: str,
        sanitize_active_content: bool = True,
    ) -> dict:
        src = self._input_for(input_handle)
        output = self._output_for(output_handle)
        if mode not in {"clean", "side_by_side"}:
            raise ValueError("invalid export mode")
        if remove not in {"redactions", "all-annotations"}:
            raise ValueError("invalid annotation removal mode")
        if not isinstance(sanitize_active_content, bool):
            raise ValueError("sanitize_active_content must be a boolean")

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
        try:
            data = self._run(args, result, self.limits.export_timeout)
            if not temp_output.is_file() or temp_output.stat().st_size > self.limits.max_output_bytes:
                raise RuntimeError("exported PDF exceeds the configured size limit")
            with temp_output.open("rb") as fh:
                os.fsync(fh.fileno())
            # Re-check the capability immediately before commit. A pre-existing
            # symlink or non-regular destination is never replaced.
            if output.exists() and (output.is_symlink() or not output.is_file()):
                raise ValueError("output path is no longer a regular file destination")
            os.replace(temp_output, output)
            return data["clean_result"]
        finally:
            temp_output.unlink(missing_ok=True)
            shutil.rmtree(work, ignore_errors=True)
