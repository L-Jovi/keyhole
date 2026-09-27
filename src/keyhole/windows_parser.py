"""Native parser handle transfer and per-process job limits."""

import ctypes
import msvcrt
import os
import subprocess
import threading
from contextlib import contextmanager
from ctypes import wintypes

from .windows_security import checked

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel.CreateJobObjectW.restype = wintypes.HANDLE
kernel.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_spawn_lock = threading.Lock()


@contextmanager
def input_handle(fd):
    with _spawn_lock:
        duplicate = wintypes.HANDLE()
        current = kernel.GetCurrentProcess()
        checked(
            kernel.DuplicateHandle(
                current, msvcrt.get_osfhandle(fd), current, ctypes.byref(duplicate), 0, True, 2
            )
        )
        try:
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [duplicate.value]}
            yield duplicate.value, startup
        finally:
            kernel.CloseHandle(duplicate)


def source_fd(handle):
    return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BasicLimits),
        ("IoInfo", ctypes.c_ulonglong * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_job = None


def limit_current_process():
    global _job
    job = kernel.CreateJobObjectW(None, None)
    checked(job)
    try:
        limits = ExtendedLimits()
        limits.BasicLimitInformation.PerProcessUserTimeLimit = 15 * 10_000_000
        limits.BasicLimitInformation.LimitFlags = 0x2 | 0x100 | 0x2000
        limits.ProcessMemoryLimit = 2 * 1024**3
        checked(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        checked(kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()))
        # The OS closes this process-owned handle on exit. Closing it here would
        # apply KILL_ON_JOB_CLOSE to this parser before its result is flushed.
        _job = job
    except BaseException:
        kernel.CloseHandle(job)
        raise
