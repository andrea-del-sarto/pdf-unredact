"""Frontend-neutral security boundary for the PDF engine.

The engine owns its temporary workspace and analysis artifacts. Frontends
(Tauri, Flutter, CLI, or the legacy local web host) pass only user-selected
input/output paths, validated options, and opaque analysis identifiers.
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
from typing import Callable, Iterable

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
        )


@dataclass(frozen=True)
class AnalysisResult:
    analysis_id: str
    pages: int
    stats: dict


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _require_within(path: Path, roots: Iterable[str | Path], label: str) -> None:
    resolved_roots = [_resolved(root) for root in roots]
    if not resolved_roots or not any(_is_within(path, root) for root in resolved_roots):
        raise ValueError(f"{label} is outside the authorized path")


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
    """Validated frontend-neutral API for the short-lived PDF worker.

    Temporary paths and audit JSON are deliberately private implementation
    details. ``analysis_id`` is an opaque handle bound to the exact input file
    metadata observed at audit time.
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
        self._analyses: dict[str, dict] = {}
        self._temp_root = Path(tempfile.mkdtemp(prefix="pdf-unredact-engine-"))
        try:
            os.chmod(self._temp_root, 0o700)
        except OSError:
            pass
        atexit.register(self.close)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._analyses.clear()
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

    @staticmethod
    def _fingerprint(path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return (str(path), int(stat.st_size), int(stat.st_mtime_ns))

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
        path = _resolved(input_path)
        if not path.is_file():
            raise ValueError("input PDF does not exist or is not a regular file")
        size = path.stat().st_size
        if size <= 0 or size > self.limits.max_input_bytes:
            raise ValueError("input PDF exceeds the configured size limit")
        with path.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise ValueError("input is not a PDF file")
        return path

    def probe(self) -> dict:
        work = self._operation_dir("probe-")
        result = work / "result.json"
        try:
            return self._run(["probe"], result, min(20, self.limits.preview_timeout))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def audit(self, input_path: str | Path) -> AnalysisResult:
        src = self.validate_input(input_path)
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
                    "fingerprint": self._fingerprint(src),
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

    def _analysis_for(self, analysis_id: str, src: Path) -> dict:
        if not isinstance(analysis_id, str) or len(analysis_id) != 32:
            raise ValueError("invalid analysis handle")
        with self._lock:
            item = self._analyses.get(analysis_id)
        if not item:
            raise ValueError("analysis handle is unavailable")
        if item["fingerprint"] != self._fingerprint(src):
            raise ValueError("input PDF changed after analysis")
        return item

    def preview(
        self,
        *,
        input_path: str | Path,
        analysis_id: str,
        output_path: str | Path,
        page: int,
        kind: str,
        remove: str,
        authorized_output_roots: Iterable[str | Path],
    ) -> Path:
        src = self.validate_input(input_path)
        analysis = self._analysis_for(analysis_id, src)
        output = _resolved(output_path)
        _require_within(output, authorized_output_roots, "preview output")
        if page < 1 or page > analysis["pages"]:
            raise ValueError("invalid page")
        if kind not in {"original", "clean"}:
            raise ValueError("invalid preview kind")
        if remove not in {"redactions", "all-annotations"}:
            raise ValueError("invalid annotation removal mode")
        ensure_free_space(output.parent, 16 * _MIB, min_free_bytes=self.limits.min_free_bytes)
        work = self._operation_dir("preview-")
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
        input_path: str | Path,
        output_path: str | Path,
        mode: str,
        remove: str,
        authorized_output_roots: Iterable[str | Path],
        sanitize_active_content: bool = True,
    ) -> dict:
        src = self.validate_input(input_path)
        output = _resolved(output_path)
        _require_within(output, authorized_output_roots, "PDF output")
        if output == src:
            raise ValueError("output PDF must be different from input PDF")
        if mode not in {"clean", "side_by_side"}:
            raise ValueError("invalid export mode")
        if remove not in {"redactions", "all-annotations"}:
            raise ValueError("invalid annotation removal mode")
        if not isinstance(sanitize_active_content, bool):
            raise ValueError("sanitize_active_content must be a boolean")
        output.parent.mkdir(parents=True, exist_ok=True)
        expected_growth = min(self.limits.max_output_bytes, max(64 * _MIB, src.stat().st_size * 3))
        ensure_free_space(output.parent, expected_growth, min_free_bytes=self.limits.min_free_bytes)
        work = self._operation_dir("export-")
        result = work / "result.json"
        args = [
            "export", "--input", str(src), "--output", str(output),
            "--mode", mode, "--remove", remove,
            "--max-pages", str(self.limits.max_pages),
            "--max-input-bytes", str(self.limits.max_input_bytes),
            "--max-output-bytes", str(self.limits.max_output_bytes),
        ]
        args.append("--sanitize-active-content" if sanitize_active_content else "--no-sanitize-active-content")
        try:
            data = self._run(args, result, self.limits.export_timeout)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if not output.is_file() or output.stat().st_size > self.limits.max_output_bytes:
            output.unlink(missing_ok=True)
            raise RuntimeError("exported PDF exceeds the configured size limit")
        return data["clean_result"]
