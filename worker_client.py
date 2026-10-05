"""Shared client for launching the short-lived PDF worker.

Used by both the web host and the CLI so untrusted PDF parsing follows the
same subprocess boundary.  The module deliberately depends only on the
standard library so it can be reused by a future desktop/Tauri frontend.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

BASE_DIR = Path(__file__).resolve().parent
WORKER_PATH = BASE_DIR / "pdf_worker.py"
DEFAULT_MAX_RESULT_BYTES = 32 * 1024 * 1024


class WorkerError(RuntimeError):
    pass


class WorkerMemoryError(WorkerError):
    pass


class WorkerCancelledError(WorkerError):
    pass


def worker_env(*, max_file_bytes: int | None = None) -> dict[str, str]:
    # Keep only variables needed for a normal Python/native-library launch.
    # In particular, do not inherit PYTHONPATH/PYTHONHOME or dynamic-loader
    # override paths (LD_LIBRARY_PATH / DYLD_LIBRARY_PATH).
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "HOME", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PDF_UNREDACT_WORKER_CPU_SECONDS"] = os.environ.get("PDF_UNREDACT_WORKER_CPU_SECONDS", "180")
    # RLIMIT_AS remains opt-in inside the worker because it measures virtual
    # address space.  RSS is monitored by the parent process instead.
    env["PDF_UNREDACT_WORKER_MEMORY_MB"] = os.environ.get("PDF_UNREDACT_WORKER_MEMORY_MB", "0")
    if max_file_bytes is None:
        env["PDF_UNREDACT_WORKER_FILE_MB"] = os.environ.get("PDF_UNREDACT_WORKER_FILE_MB", "2048")
    else:
        env["PDF_UNREDACT_WORKER_FILE_MB"] = str(max(1, (int(max_file_bytes) + 1024 * 1024 - 1) // (1024 * 1024)))
    env["PDF_UNREDACT_WORKER_NOFILE"] = os.environ.get("PDF_UNREDACT_WORKER_NOFILE", "256")
    return env


def _rss_linux(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _rss_macos(pid: int) -> int | None:
    try:
        out = subprocess.check_output(
            ["/bin/ps", "-o", "rss=", "-p", str(pid)],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            text=True,
        ).strip()
        return int(out) * 1024 if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _rss_windows(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            return None
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def process_rss_bytes(pid: int) -> int | None:
    if sys.platform.startswith("linux"):
        return _rss_linux(pid)
    if sys.platform == "darwin":
        return _rss_macos(pid)
    if os.name == "nt":
        return _rss_windows(pid)
    return None


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_worker(
    args: list[str],
    result_path: str | Path,
    timeout: int,
    *,
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
    max_rss_mb: int | None = None,
    max_file_bytes: int | None = None,
    concurrency_guard=None,
    log_debug: Callable[[str], None] | None = None,
    log_error: Callable[[str], None] | None = None,
    cancel_event=None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    if not WORKER_PATH.is_file():
        raise WorkerError("PDF worker is unavailable")

    result_path = Path(result_path)
    result_path.unlink(missing_ok=True)
    cmd = [sys.executable, str(WORKER_PATH), *args, "--result", str(result_path)]
    if max_rss_mb is None:
        try:
            max_rss_mb = max(0, int(os.environ.get("PDF_UNREDACT_WORKER_MAX_RSS_MB", "1536")))
        except ValueError:
            max_rss_mb = 1536
    rss_limit = max_rss_mb * 1024 * 1024 if max_rss_mb else 0

    class _NullGuard:
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    guard = concurrency_guard if concurrency_guard is not None else _NullGuard()
    def emit(phase: str, **extra) -> None:
        if progress_callback:
            payload = {"phase": phase, **extra}
            try:
                progress_callback(payload)
            except Exception:
                pass

    stderr_file = tempfile.TemporaryFile()
    emit("worker_starting")
    with guard:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            env=worker_env(max_file_bytes=max_file_bytes),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            start_new_session=(os.name != "nt"),
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
        emit("worker_running", pid=proc.pid)
        deadline = time.monotonic() + timeout
        next_rss_check = 0.0
        while proc.poll() is None:
            now = time.monotonic()
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(proc)
                stderr_file.close()
                emit("cancelled")
                raise WorkerCancelledError("PDF worker operation was cancelled")
            if now >= deadline:
                _terminate_process(proc)
                stderr_file.close()
                emit("timeout")
                raise TimeoutError("PDF worker timed out")
            if rss_limit and now >= next_rss_check:
                rss = process_rss_bytes(proc.pid)
                if rss is not None and rss > rss_limit:
                    _terminate_process(proc)
                    stderr_file.close()
                    emit("memory_limit")
                    raise WorkerMemoryError(
                        f"PDF worker exceeded the resident-memory limit ({max_rss_mb} MB)"
                    )
                next_rss_check = now + (0.5 if sys.platform == "darwin" else 0.2)
            time.sleep(0.05)

    stderr_file.seek(0)
    stderr_text = stderr_file.read().decode("utf-8", "replace")[-8000:]
    stderr_file.close()
    if log_debug and stderr_text:
        log_debug(stderr_text)

    if not result_path.exists():
        if log_error:
            if proc.returncode is not None and proc.returncode < 0:
                log_error(f"PDF worker terminated by signal {-proc.returncode}")
            else:
                log_error(f"PDF worker exited without a result (exit {proc.returncode})")
        raise WorkerError("PDF worker did not start or terminated unexpectedly")
    if result_path.stat().st_size > max_result_bytes:
        raise WorkerError("PDF worker result is too large")
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError("PDF worker returned an invalid result") from exc
    if not data.get("ok"):
        error = WorkerError(data.get("error") or "PDF worker failed")
        setattr(error, "worker_error_type", data.get("error_type"))
        raise error
    emit("worker_completed")
    return data
