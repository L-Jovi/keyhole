"""Native Windows file primitives; not yet enabled by the public CLI.

Every relative open uses a held directory handle and one validated component.
The POSIX rename flag permits replacement without closing the old file handle.
See Microsoft's NtCreateFile and FILE_RENAME_INFORMATION documentation.
"""

import ctypes
import errno
import os
import re
import sys
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import PureWindowsPath

if sys.platform != "win32":
    raise ImportError("Windows file primitives require native Windows.")

import msvcrt  # noqa: E402

FILE_LIST_DIRECTORY = 0x0001
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_TRAVERSE = 0x0020
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
DELETE = 0x00010000
READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
FILE_SHARE_DELETE = 4
FILE_DIRECTORY_FILE = 0x00000001
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_OPEN = 1
FILE_CREATE = 2
OBJ_CASE_INSENSITIVE = 0x40
FILE_RENAME_INFORMATION_EX = 65
FILE_RENAME_REPLACE_IF_EXISTS = 1
FILE_RENAME_POSIX_SEMANTICS = 2


class UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(UnicodeString)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class IoStatusBlock(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class FileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", wintypes.FILETIME),
        ("accessed", wintypes.FILETIME),
        ("written", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


class RenameInformation(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


kernel = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")
kernel.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL
kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
kernel.GetFileInformationByHandle.restype = wintypes.BOOL
kernel.GetVolumeInformationByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPWSTR,
    wintypes.DWORD,
]
kernel.GetVolumeInformationByHandleW.restype = wintypes.BOOL
kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
kernel.GetDriveTypeW.restype = wintypes.UINT
ntdll.NtCreateFile.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    ctypes.POINTER(ObjectAttributes),
    ctypes.POINTER(IoStatusBlock),
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
ntdll.NtCreateFile.restype = wintypes.LONG
ntdll.NtSetInformationFile.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(IoStatusBlock),
    ctypes.c_void_p,
    wintypes.ULONG,
    wintypes.ULONG,
]
ntdll.NtSetInformationFile.restype = wintypes.LONG
ntdll.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG


def check_status(status: int) -> None:
    if status < 0:
        error = ctypes.WinError(ntdll.RtlNtStatusToDosError(status))
        error.add_note(f"NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
        raise error


def component(name: str) -> None:
    # NT names can bypass Win32's normal checks; never expose those namespaces remotely.
    stem = name.partition(".")[0].upper()
    if (
        not name
        or name in (".", "..")
        or name[-1:] in (".", " ")
        or any(c in name for c in '\\/:<>"|?*')
        or any(ord(c) < 32 for c in name)
        or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)
        or re.search(r"~[0-9]+(?:\.|$)", name)
        or len(name.encode("utf-16-le")) > 510
    ):
        raise ValueError(
            "Use a normal filename, without device names, streams or short-name aliases."
        )


class Handle:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.value is not None:
            value, self.value = self.value, None
            if not kernel.CloseHandle(value):
                raise ctypes.WinError(ctypes.get_last_error())

    def info(self):
        result = FileInformation()
        if not kernel.GetFileInformationByHandle(self.value, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def identity(self):
        info = self.info()
        return info.volume, (info.index_high << 32) | info.index_low

    def validate(self, *, directory):
        info = self.info()
        if info.attributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_OFFLINE):
            raise OSError(errno.ELOOP, "Reparse points and offline placeholders are refused.")
        if directory is not None and bool(info.attributes & FILE_ATTRIBUTE_DIRECTORY) != directory:
            raise OSError("Unexpected file type.")
        if not info.attributes & FILE_ATTRIBUTE_DIRECTORY and info.links != 1:
            raise OSError("Only single-link regular files are allowed.")
        if info.attributes & FILE_ATTRIBUTE_DIRECTORY:
            flags = wintypes.DWORD()
            if not kernel.GetFileInformationByHandleEx(self.value, 23, ctypes.byref(flags), 4):
                raise ctypes.WinError(ctypes.get_last_error())
            if flags.value & 1:
                raise OSError("Case-sensitive NTFS directories are not supported.")

    def detach_fd(self, *, writable=False):
        flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_BINARY
        fd = msvcrt.open_osfhandle(self.value, flags)
        self.value = None
        return fd


def volume(anchor: str) -> Handle:
    if not re.fullmatch(r"[A-Za-z]:\\", anchor):
        raise ValueError("Only an absolute path on a local NTFS drive is supported.")
    if kernel.GetDriveTypeW(anchor) not in (2, 3):
        raise OSError("Network drives and device namespaces are refused.")
    value = kernel.CreateFileW(
        anchor,
        FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | READ_CONTROL,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        3,
        0x02200000,
        None,
    )
    if value == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = Handle(value)
    try:
        result.validate(directory=True)
        filesystem = ctypes.create_unicode_buffer(64)
        if not kernel.GetVolumeInformationByHandleW(
            value, None, 0, None, None, None, filesystem, 64
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if filesystem.value != "NTFS":
            raise OSError("Only local NTFS volumes are supported.")
        return result
    except BaseException:
        result.close()
        raise


def child(
    parent: Handle,
    name: str,
    *,
    directory=False,
    write=False,
    delete=False,
    create=False,
    security: bytes | None = None,
):
    component(name)
    if security is not None and not create:
        raise ValueError("An initial ACL is only valid when creating a new entry.")
    descriptor = ctypes.create_string_buffer(security) if security is not None else None
    buffer = ctypes.create_unicode_buffer(name)
    size = len(name.encode("utf-16-le"))
    string = UnicodeString(size, size + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        parent.value,
        ctypes.pointer(string),
        OBJ_CASE_INSENSITIVE,
        ctypes.cast(descriptor, ctypes.c_void_p) if descriptor is not None else None,
        None,
    )
    desired = READ_CONTROL | SYNCHRONIZE | FILE_READ_ATTRIBUTES
    desired |= FILE_LIST_DIRECTORY | FILE_TRAVERSE if directory else FILE_READ_DATA
    if write:
        desired |= FILE_WRITE_DATA | FILE_WRITE_ATTRIBUTES
    if delete:
        desired |= DELETE
    if create:
        desired |= WRITE_DAC
    value, status = wintypes.HANDLE(), IoStatusBlock()
    check_status(
        ntdll.NtCreateFile(
            ctypes.byref(value),
            desired,
            ctypes.byref(attributes),
            ctypes.byref(status),
            None,
            FILE_ATTRIBUTE_DIRECTORY if directory else 0x80,
            FILE_SHARE_READ | FILE_SHARE_WRITE | (0 if directory else FILE_SHARE_DELETE),
            FILE_CREATE if create else FILE_OPEN,
            FILE_OPEN_REPARSE_POINT
            | FILE_SYNCHRONOUS_IO_NONALERT
            | (
                FILE_DIRECTORY_FILE
                if directory
                else FILE_NON_DIRECTORY_FILE
                if directory is False
                else 0
            ),
            None,
            0,
        )
    )
    result = Handle(value.value)
    try:
        result.validate(directory=directory)
        return result
    except BaseException:
        result.close()
        raise


@contextmanager
def walk(path):
    path = PureWindowsPath(path)
    handles = []
    try:
        handles.append(volume(path.anchor))
        for name in path.parts[1:]:
            handles.append(child(handles[-1], name, directory=True))
        yield handles[-1]
    finally:
        for item in reversed(handles):
            item.close()


def rename(source: Handle, parent: Handle, name: str, *, replace=False):
    component(name)
    encoded = name.encode("utf-16-le")
    size = ctypes.sizeof(RenameInformation) + len(encoded)
    buffer = ctypes.create_string_buffer(size)
    info = RenameInformation.from_buffer(buffer)
    info.Flags = FILE_RENAME_REPLACE_IF_EXISTS | FILE_RENAME_POSIX_SEMANTICS if replace else 0
    info.RootDirectory = parent.value
    info.FileNameLength = len(encoded)
    ctypes.memmove(
        ctypes.addressof(buffer) + RenameInformation.FileName.offset, encoded, len(encoded)
    )
    status = IoStatusBlock()
    check_status(
        ntdll.NtSetInformationFile(
            source.value,
            ctypes.byref(status),
            buffer,
            size,
            FILE_RENAME_INFORMATION_EX,
        )
    )


class DirectoryInformation(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.DWORD),
        ("FileIndex", wintypes.DWORD),
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("AllocationSize", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
        ("FileNameLength", wintypes.DWORD),
        ("EaSize", wintypes.DWORD),
        ("ShortNameLength", ctypes.c_byte),
        ("ShortName", wintypes.WCHAR * 12),
        ("FileId", ctypes.c_longlong),
        ("FileName", wintypes.WCHAR * 1),
    ]


kernel.GetFileInformationByHandleEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
kernel.GetFinalPathNameByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD


def names(directory: Handle):
    """Enumerate through the same held directory used for relative opens."""
    buffer = ctypes.create_string_buffer(65536)
    info_class = 11  # FileIdBothDirectoryRestartInfo, then FileIdBothDirectoryInfo.
    while True:
        if not kernel.GetFileInformationByHandleEx(
            directory.value, info_class, buffer, len(buffer)
        ):
            error = ctypes.get_last_error()
            if error == 18:  # ERROR_NO_MORE_FILES
                return
            raise ctypes.WinError(error)
        info_class = 10
        offset = 0
        while True:
            info = DirectoryInformation.from_buffer(buffer, offset)
            start = offset + DirectoryInformation.FileName.offset
            end = start + info.FileNameLength
            if info.FileNameLength % 2 or end > len(buffer):
                raise OSError("Invalid native directory enumeration.")
            name = buffer.raw[start:end].decode("utf-16-le")
            if name not in (".", ".."):
                yield name
            if not info.NextEntryOffset:
                break
            if info.NextEntryOffset < DirectoryInformation.FileName.offset:
                raise OSError("Invalid native directory offset.")
            offset += info.NextEntryOffset


def final_path(handle: Handle) -> str:
    size = kernel.GetFinalPathNameByHandleW(handle.value, None, 0, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size + 1)
    length = kernel.GetFinalPathNameByHandleW(handle.value, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise OSError("The final path changed while it was queried.")
    path = buffer.value
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return path


def remove(handle: Handle):
    # FILE_DISPOSITION_INFORMATION_EX, DELETE | POSIX_SEMANTICS.
    flags, status = wintypes.DWORD(1 | 2), IoStatusBlock()
    check_status(
        ntdll.NtSetInformationFile(handle.value, ctypes.byref(status), ctypes.byref(flags), 4, 64)
    )


def mutation_supported(handle: Handle):
    # Replacing a file cannot silently discard EFS encryption, sparse/compressed
    # storage or another named data stream, including Mark-of-the-Web metadata.
    if handle.info().attributes & (0x1 | 0x200 | 0x800 | 0x4000):
        raise OSError("Read-only, encrypted, compressed and sparse files cannot be edited.")
    buffer = ctypes.create_string_buffer(65536)
    if not kernel.GetFileInformationByHandleEx(handle.value, 7, buffer, len(buffer)):
        raise ctypes.WinError(ctypes.get_last_error())
    offset = 0
    while True:
        next_offset = wintypes.DWORD.from_buffer(buffer, offset).value
        length = wintypes.DWORD.from_buffer(buffer, offset + 4).value
        if length % 2 or offset + 24 + length > len(buffer):
            raise OSError("Invalid stream metadata.")
        name = buffer.raw[offset + 24 : offset + 24 + length].decode("utf-16-le")
        if name != "::$DATA":
            raise OSError("Files with alternate data streams cannot be edited or deleted.")
        if not next_offset:
            return
        if next_offset < 24:
            raise OSError("Invalid stream offset.")
        offset += next_offset
