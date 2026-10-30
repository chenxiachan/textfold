"""Cancellation of one owned agent process tree, never a process-name search."""
import os
import signal
import subprocess
import tempfile
import time


class _WindowsJob:
    def __init__(self, process):
        import ctypes as c
        from ctypes import wintypes as w
        class Limits(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong), ("flags", w.DWORD),
                        ("min_working", c.c_size_t), ("max_working", c.c_size_t), ("active", w.DWORD),
                        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Counters(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ("read", "write", "other", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(c.Structure):
            _fields_ = [("basic", Limits), ("io", Counters), ("process_memory", c.c_size_t),
                        ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = (c.c_void_p, w.LPCWSTR)
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = (w.HANDLE, c.c_int, c.c_void_p, w.DWORD)
        self.kernel.AssignProcessToJobObject.argtypes = (w.HANDLE, w.HANDLE)
        self.kernel.CloseHandle.argtypes = (w.HANDLE,)
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle: raise c.WinError(c.get_last_error())
        limits = Extended(); limits.basic.flags = 0x2000   # kill only this job's descendants on close
        if not self.kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)) or \
                not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = c.WinError(c.get_last_error()); self.close(); raise error

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def resume(self, process):
        """Resume only this Popen's initial thread, after job assignment.

        Popen closes CreateProcess's thread handle, so find it by the still-live,
        suspended process ID. No other process's thread is opened or resumed.
        """
        import ctypes as c
        from ctypes import wintypes as w
        class Thread(c.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("id", w.DWORD),
                        ("owner", w.DWORD), ("priority", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]
        kernel = self.kernel
        kernel.CreateToolhelp32Snapshot.argtypes = (w.DWORD, w.DWORD)
        kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
        kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = (w.HANDLE, c.POINTER(Thread))
        kernel.OpenThread.argtypes = (w.DWORD, w.BOOL, w.DWORD)
        kernel.OpenThread.restype = w.HANDLE
        kernel.ResumeThread.argtypes = (w.HANDLE,)
        kernel.ResumeThread.restype = w.DWORD
        snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == w.HANDLE(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            thread = Thread(); thread.size = c.sizeof(thread)
            found = kernel.Thread32First(snapshot, c.byref(thread))
            while found:
                if thread.owner == process.pid:
                    handle = kernel.OpenThread(0x2, False, thread.id)  # THREAD_SUSPEND_RESUME
                    if not handle: raise c.WinError(c.get_last_error())
                    try:
                        count = kernel.ResumeThread(handle)
                        if count == 0xffffffff: raise c.WinError(c.get_last_error())
                        if count == 1: return
                    finally:
                        kernel.CloseHandle(handle)
                found = kernel.Thread32Next(snapshot, c.byref(thread))
            raise RuntimeError("Could not resume the owned agent's suspended thread")
        finally:
            kernel.CloseHandle(snapshot)


def run(args, *, stop, timeout, **kwargs):
    """communicate with polling cancellation; output completed before Stop is retained."""
    from .llm import Stopped
    if stop.is_set(): raise Stopped()
    body = kwargs.pop("input", None)
    windows = os.name == "nt"
    # CREATE_SUSPENDED prevents descendants escaping before AssignProcessToJobObject.
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW | 0x4} if windows else {"start_new_session": True}
    source = tempfile.TemporaryFile()
    try:
        source.write(body.encode(kwargs.get("encoding") or "utf-8") if isinstance(body, str) else body or b"")
        source.seek(0)
        process = subprocess.Popen(args, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags, **kwargs)
    except BaseException:
        source.close()
        raise
    job = None
    try:
        if windows:
            job = _WindowsJob(process)
            if stop.is_set(): raise Stopped()
            job.resume(process)
        deadline = time.monotonic() + timeout
        while True:
            if stop.is_set() and process.poll() is None: raise Stopped()
            left = deadline - time.monotonic()
            if left <= 0: raise subprocess.TimeoutExpired(args, timeout)
            try:
                out, err = process.communicate(timeout=min(.2, left))
                return subprocess.CompletedProcess(args, process.returncode, out, err)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if job:
            job.close()
        elif not windows:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        if process.poll() is None:
            process.kill()   # the handle from this Popen, never an unrelated bridge
        try: process.communicate(timeout=5)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream: stream.close()
            source.close()
