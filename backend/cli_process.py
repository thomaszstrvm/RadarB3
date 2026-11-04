"""Bounded CLI process transport. No shell or credential-file access."""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time
from dataclasses import dataclass


class CLIError(RuntimeError):
    def __init__(self, code, *, uncertain=False, repairable=False):
        super().__init__(code)
        self.code, self.uncertain, self.repairable = code, uncertain, repairable


@dataclass
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class WindowsProcessJob:
    """An unnamed Job Object contains only this freshly launched CLI subtree.

    Assignment happens before stdin sends any evidence or model instruction.
    Closing the job kills descendants even if the parent CLI has already exited.
    Failure to establish containment is fail-closed; there is no taskkill fallback.
    """
    def __init__(self, process):
        if os.name != "nt":
            raise CLIError("cli_process_containment_requires_windows")
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong), ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IOCounters), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise CLIError("cli_process_containment_failed")
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            self.close()
            raise CLIError("cli_process_containment_failed")

    def close(self):
        if getattr(self, "handle", None):
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class AnalysisMutex:
    """One active analysis across app profiles/processes in this Windows session."""
    _thread_lock = threading.Lock()

    def __enter__(self):
        if not self._thread_lock.acquire(blocking=False):
            raise CLIError("cli_analysis_already_running")
        self.handle = None
        try:
            if os.name != "nt":
                raise CLIError("cli_platform_not_homologated")
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.CreateMutexW(None, False, "Local\\RadarB3SingleAnalysisV1")
            if not self.handle or self.kernel.WaitForSingleObject(self.handle, 0) not in {0, 0x80}:
                raise CLIError("cli_analysis_already_running")
            return self
        except BaseException:
            if self.handle:
                self.kernel.CloseHandle(self.handle)
            self._thread_lock.release()
            raise

    def __exit__(self, *_):
        try:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
        finally:
            self._thread_lock.release()


def execute_bounded(args, *, stdin, cwd, env, timeout=300, cancel_check=lambda: None,
                    max_stdout=2_000_000, max_stderr=64_000,
                    popen_factory=subprocess.Popen, job_factory=WindowsProcessJob):
    """Capture finite bytes with a deadline and cooperative cancellation.

    Output is memory-only and never logged. Containment is attached before the
    stdin writer starts. Tests inject transports; production cannot disable it.
    """
    cancel_check()
    process = None
    job = None
    threads = []
    overflow = threading.Event()
    stdout, stderr = bytearray(), bytearray()
    sent = threading.Event()
    deadline = time.monotonic() + timeout

    def read_pipe(pipe, target, limit):
        try:
            while True:
                data = pipe.read(4096)
                if not data:
                    break
                remaining = limit - len(target)
                target.extend(data[:max(0, remaining)])
                if len(data) > remaining:
                    overflow.set()
                    break
        except (OSError, ValueError):
            pass

    def write_input():
        try:
            sent.set()  # Conservative: a failed write may still send a prefix.
            process.stdin.write(stdin)
            process.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    try:
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
        process = popen_factory(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(cwd), env=env, shell=False, close_fds=True, creationflags=flags)
        job = job_factory(process)
        threads = [threading.Thread(target=read_pipe, args=(process.stdout, stdout, max_stdout), daemon=True),
                   threading.Thread(target=read_pipe, args=(process.stderr, stderr, max_stderr), daemon=True),
                   threading.Thread(target=write_input, daemon=True)]
        for thread in threads:
            thread.start()
        while process.poll() is None:
            cancel_check()
            if overflow.is_set():
                raise CLIError("cli_output_size_limit", uncertain=sent.is_set())
            if time.monotonic() >= deadline:
                raise CLIError("cli_timeout_usage_uncertain", uncertain=sent.is_set())
            time.sleep(0.05)
        # Closing the job also closes pipes inherited by any remaining children.
        job.close()
        job = None
        for thread in threads:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in threads):
            raise CLIError("cli_output_drain_failed", uncertain=sent.is_set())
        if overflow.is_set():
            raise CLIError("cli_output_size_limit", uncertain=sent.is_set())
        cancel_check()
        return ProcessResult(process.returncode, bytes(stdout), bytes(stderr))
    except InterruptedError:
        raise
    except OSError:
        raise CLIError("cli_process_launch_failed", uncertain=sent.is_set()) from None
    finally:
        if job is not None:
            job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()  # Only this Popen handle; never an arbitrary PID.
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (process.stdin, process.stdout, process.stderr):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
