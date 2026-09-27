"""Windows boot and process identity using OS queries, without a supervisor."""

import ctypes
import json
import os
import subprocess
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

from .errors import KeyholeError, require
from .windows_security import checked, user_sid

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
shell = ctypes.WinDLL("shell32", use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
kernel.LocalFree.argtypes = [ctypes.c_void_p]


def query(script):
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); " + script
    )
    try:
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            encoding="utf-8",
            timeout=20,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise KeyholeError(
            "windows_process_query_failed",
            "Windows could not verify boot/process identity. Check Windows PowerShell and WMI "
            "availability. Sharing stays closed; no unverified process is terminated.",
        ) from exc


@lru_cache(maxsize=1)
def boot_id():
    # CIM exposes the last boot time; a process cannot survive a reboot, so the
    # per-process cache cannot carry an authorization across boots.
    value = query(
        "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks | ConvertTo-Json"
    )
    require(
        type(value) is int and value > 0,
        "boot_identity_unavailable",
        "Invalid Windows boot identity.",
    )
    return str(value)


def split_command(command):
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    checked(pointer)
    try:
        return [pointer[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(pointer)


def creation_time(process):
    created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    checked(
        kernel.GetProcessTimes(
            process,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
    )
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def owned_processes(state_dir=None):
    entries = query("""
    $items = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
      Where-Object { $_.CommandLine -match 'keyhole[.](server|parsers)' } |
      ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid
        if ($owner.ReturnValue -ne 0) { throw 'Cannot verify process owner' }
        [pscustomobject]@{
          pid = [int]$_.ProcessId; command = $_.CommandLine; owner = $owner.Sid
          created = $_.CreationDate.ToUniversalTime().ToFileTimeUtc()
        }
      })
    ConvertTo-Json -InputObject $items -Compress
    """)
    require(isinstance(entries, list), "windows_process_query_failed", "Invalid process listing.")
    result = {}
    for entry in entries:
        if entry["owner"] != user_sid():
            continue
        words = split_command(entry["command"])
        if not any(
            words[i : i + 2] in (["-m", "keyhole.server"], ["-m", "keyhole.parsers"])
            for i in range(len(words))
        ):
            continue
        if state_dir is not None and not any(
            words[i : i + 2] == ["--state-dir", str(state_dir)] for i in range(len(words))
        ):
            continue
        process = kernel.OpenProcess(0x1000, False, entry["pid"])
        if not process:
            if ctypes.get_last_error() == 87:  # Process has exited.
                continue
            checked(process)
        try:
            created = creation_time(process)
            # CIM timestamps have microsecond precision; the open handle supplies
            # the exact 100 ns identity used when terminating this process later.
            if created // 10 != entry["created"] // 10:
                continue
            result[entry["pid"]] = (created, entry["command"])
        finally:
            kernel.CloseHandle(process)
    return result


def terminate_owned(state_dir):
    for pid, (created, _command) in owned_processes(state_dir).items():
        process = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, pid)
        if not process:
            if ctypes.get_last_error() == 87:
                continue
            checked(process)
        try:
            if creation_time(process) == created:
                checked(kernel.TerminateProcess(process, 1))
                kernel.WaitForSingleObject(process, 3000)
        finally:
            kernel.CloseHandle(process)
