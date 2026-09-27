"""Windows ownership and private ACLs; no account or privilege changes."""

import ctypes
import sys
from ctypes import wintypes
from functools import lru_cache

if sys.platform != "win32":
    raise ImportError("Windows ACLs require native Windows.")

OWNER_SECURITY_INFORMATION = 1
DACL_SECURITY_INFORMATION = 4
SE_FILE_OBJECT = 1
SE_DACL_PROTECTED = 0x1000
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
FILE_ALL_ACCESS = 0x001F01FF
ACCESS_ALLOWED_ACE_TYPE = 0
INHERIT_ONLY_ACE = 0x08

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.LocalFree.argtypes = [ctypes.c_void_p]
kernel.LocalFree.restype = ctypes.c_void_p
advapi.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
advapi.GetTokenInformation.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.DWORD),
]
advapi.GetSecurityInfo.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.c_void_p),
]
advapi.GetSecurityInfo.restype = wintypes.DWORD
advapi.GetSecurityDescriptorLength.argtypes = [ctypes.c_void_p]
advapi.GetSecurityDescriptorLength.restype = wintypes.DWORD
advapi.IsValidSecurityDescriptor.argtypes = [ctypes.c_void_p]
advapi.GetSecurityDescriptorOwner.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.BOOL),
]
advapi.GetSecurityDescriptorDacl.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.BOOL),
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.BOOL),
]
advapi.GetSecurityDescriptorControl.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.USHORT),
    ctypes.POINTER(wintypes.DWORD),
]
advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
advapi.SetSecurityInfo.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
advapi.SetSecurityInfo.restype = wintypes.DWORD


def checked(value):
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


def sid_string(pointer) -> str:
    result = wintypes.LPWSTR()
    checked(advapi.ConvertSidToStringSidW(pointer, ctypes.byref(result)))
    try:
        return result.value
    finally:
        kernel.LocalFree(ctypes.cast(result, ctypes.c_void_p))


@lru_cache(maxsize=1)
def user_sid() -> str:
    token = wintypes.HANDLE()
    checked(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)))
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        checked(advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)))
        return sid_string(ctypes.c_void_p.from_buffer(buffer))
    finally:
        kernel.CloseHandle(token)


def descriptor_from_sddl(value: str) -> bytes:
    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    checked(
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            value,
            1,
            ctypes.byref(descriptor),
            ctypes.byref(size),
        )
    )
    try:
        return ctypes.string_at(descriptor, size.value)
    finally:
        kernel.LocalFree(descriptor)


@lru_cache(maxsize=1)
def private_descriptor() -> bytes:
    sid = user_sid()
    # SYSTEM and Administrators already control the host; ordinary other users get no access.
    return descriptor_from_sddl(f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)")


def snapshot(handle: int) -> bytes:
    descriptor = ctypes.c_void_p()
    error = advapi.GetSecurityInfo(
        handle,
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        return ctypes.string_at(descriptor, advapi.GetSecurityDescriptorLength(descriptor))
    finally:
        kernel.LocalFree(descriptor)


def descriptor_buffer(value: bytes):
    if not isinstance(value, bytes) or not 20 <= len(value) <= 65536:
        raise ValueError("Invalid security descriptor size.")
    buffer = ctypes.create_string_buffer(value)
    if not advapi.IsValidSecurityDescriptor(buffer):
        raise ValueError("Invalid security descriptor.")
    return buffer


def owner(value: bytes) -> str:
    buffer = descriptor_buffer(value)
    pointer, defaulted = ctypes.c_void_p(), wintypes.BOOL()
    checked(
        advapi.GetSecurityDescriptorOwner(buffer, ctypes.byref(pointer), ctypes.byref(defaulted))
    )
    if not pointer.value:
        raise ValueError("A file owner is required.")
    return sid_string(pointer)


class Acl(ctypes.Structure):
    _fields_ = [
        ("revision", wintypes.BYTE),
        ("reserved", wintypes.BYTE),
        ("size", wintypes.USHORT),
        ("count", wintypes.USHORT),
        ("reserved2", wintypes.USHORT),
    ]


class AllowedAce(ctypes.Structure):
    _fields_ = [
        ("kind", wintypes.BYTE),
        ("flags", wintypes.BYTE),
        ("size", wintypes.USHORT),
        ("mask", wintypes.DWORD),
        ("sid", wintypes.DWORD),
    ]


def is_private(value: bytes) -> bool:
    if owner(value) != user_sid():
        return False
    buffer = descriptor_buffer(value)
    present, defaulted, pointer = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
    checked(
        advapi.GetSecurityDescriptorDacl(
            buffer,
            ctypes.byref(present),
            ctypes.byref(pointer),
            ctypes.byref(defaulted),
        )
    )
    if not present.value or not pointer.value:
        return False
    acl = Acl.from_address(pointer.value)
    owner_access = 0
    for index in range(acl.count):
        entry = ctypes.c_void_p()
        checked(advapi.GetAce(pointer, index, ctypes.byref(entry)))
        ace = AllowedAce.from_address(entry.value)
        if ace.kind != ACCESS_ALLOWED_ACE_TYPE or ace.size < ctypes.sizeof(AllowedAce):
            return False
        sid = sid_string(entry.value + AllowedAce.sid.offset)
        if sid not in {user_sid(), "S-1-5-18", "S-1-5-32-544"}:
            return False
        if sid == user_sid() and not ace.flags & INHERIT_ONLY_ACE:
            owner_access |= ace.mask
    return owner_access & FILE_ALL_ACCESS == FILE_ALL_ACCESS


def restore_dacl(handle: int, value: bytes) -> None:
    buffer = descriptor_buffer(value)
    if owner(value) != user_sid():
        raise ValueError("Cannot assign another user's recovery ACL.")
    control, revision = wintypes.USHORT(), wintypes.DWORD()
    checked(
        advapi.GetSecurityDescriptorControl(buffer, ctypes.byref(control), ctypes.byref(revision))
    )
    flags = DACL_SECURITY_INFORMATION | (
        PROTECTED_DACL_SECURITY_INFORMATION
        if control.value & SE_DACL_PROTECTED
        else UNPROTECTED_DACL_SECURITY_INFORMATION
    )
    present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
    checked(
        advapi.GetSecurityDescriptorDacl(
            buffer, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
        )
    )
    if not present.value or not acl.value:
        raise ValueError("Cannot restore a missing or null file DACL.")
    # File objects need SetSecurityInfo so inherited ACLs retain automatic
    # inheritance. SetKernelObjectSecurity drops that state on ordinary files.
    error = advapi.SetSecurityInfo(handle, SE_FILE_OBJECT, flags, None, None, acl, None)
    if error:
        raise ctypes.WinError(error)


def file_dacl_signature(value: bytes):
    """Compare file access without container-only inheritance bookkeeping."""
    buffer = descriptor_buffer(value)
    present, defaulted, pointer = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
    checked(
        advapi.GetSecurityDescriptorDacl(
            buffer, ctypes.byref(present), ctypes.byref(pointer), ctypes.byref(defaulted)
        )
    )
    if not present.value or not pointer.value:
        raise ValueError("A recoverable file must have a non-null DACL.")
    control, revision = wintypes.USHORT(), wintypes.DWORD()
    checked(
        advapi.GetSecurityDescriptorControl(buffer, ctypes.byref(control), ctypes.byref(revision))
    )
    acl = Acl.from_address(pointer.value)
    entries = []
    for index in range(acl.count):
        entry = ctypes.c_void_p()
        checked(advapi.GetAce(pointer, index, ctypes.byref(entry)))
        header = AllowedAce.from_address(entry.value)
        if header.size < 4 or entry.value + header.size > pointer.value + acl.size:
            raise ValueError("Invalid file DACL entry.")
        data = bytearray(ctypes.string_at(entry, header.size))
        # Files have no children. SetSecurityInfo removes OI/CI/NP and records
        # auto-inheritance. Preserve every effective right, SID, ACE order,
        # inherited/inherit-only flag and protection against parent changes.
        data[1] &= ~0x07
        entries.append(bytes(data))
    return owner(value), control.value & SE_DACL_PROTECTED, tuple(entries)


advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(wintypes.DWORD),
]


def to_sddl(value: bytes) -> str:
    buffer = descriptor_buffer(value)
    output, size = wintypes.LPWSTR(), wintypes.DWORD()
    checked(
        advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            buffer,
            1,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(output),
            ctypes.byref(size),
        )
    )
    try:
        return output.value
    finally:
        kernel.LocalFree(ctypes.cast(output, ctypes.c_void_p))
