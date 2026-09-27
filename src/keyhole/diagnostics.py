"""Human guidance and an allowlisted issue report, separate from the stable status JSON."""

import re

from . import __version__

ERROR_CODES = frozenset(
    {
        "not_configured",
        "offline",
        "native_client_missing",
        "native_client_failed",
        "runtime_config",
        "runtime_key_permissions",
        "state_permissions",
        "client_version_changed",
        "runtime_timeout",
        "native_runtime_failed",
        "runtime_not_ready",
        "permission_denied",
        "path_missing",
        "symlink_in_path",
        "invalid_path",
        "state_invalid",
        "boot_identity_unavailable",
        "windows_process_query_failed",
        "unsupported_platform",
        "local_failure",
        "manager_busy",
    }
)


def error_code(value: dict) -> str:
    code = value.get("code")
    return code if isinstance(code, str) and code in ERROR_CODES else "unknown_error"


def redact_status(result: dict) -> dict:
    """Never copy free text, paths, names, ids, native diagnostics, or future fields."""
    checks = result.get("checks", {})
    runtime = result.get("runtime", {})
    safe_checks = {"ok": checks.get("ok") is True}
    for name in ("client_version", "accepted_client_version"):
        value = checks.get(name)
        if isinstance(value, str) and re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", value):
            safe_checks[name] = value
    for name in ("client_error", "config_error"):
        if isinstance(checks.get(name), dict):
            safe_checks[name] = {"code": error_code(checks[name])}
    safe_runtime = {
        name: runtime.get(name) if type(runtime.get(name)) is bool else None
        for name in ("process_running", "healthy", "ready", "stale")
    }
    if isinstance(runtime.get("error"), dict):
        safe_runtime["error"] = {"code": error_code(runtime["error"])}
    result_safe = {
        "redacted": True,
        "keyhole_version": __version__,
        "configured": result.get("configured") is True,
        "checks": safe_checks,
        "runtime": safe_runtime,
        "open_workspace_count": len(result.get("effective_open_workspaces", [])),
    }
    if isinstance(result.get("error"), dict):
        result_safe["error"] = {"code": error_code(result["error"])}
    return result_safe


def human_status(result: dict, setup_command: str) -> str:
    checks, runtime = result.get("checks", {}), result.get("runtime", {})
    lines = ["Keyhole status"]
    lines.append(f"Client: {checks.get('tunnel_client') or 'not found'}")
    if checks.get("client_version"):
        lines.append(f"Client version: {checks['client_version']}")
    if "client_error" in checks or "config_error" in checks or checks.get("ok") is not True:
        lines.append("Local setup needs attention.")
        for key in ("client_error", "config_error"):
            if key in checks:
                lines.append(checks[key]["message"])
        if checks.get("client_version") != checks.get("accepted_client_version"):
            lines.append("The installed client version differs from the accepted version.")
        lines.append(f"Next: {setup_command}")
    elif runtime.get("ready") and runtime.get("process_running"):
        names = result.get("effective_open_workspaces", [])
        lines.append(f"Tunnel ready. Open workspaces: {', '.join(names) or 'none'}.")
        lines.append(
            "Next: select your private Keyhole app in ChatGPT and read a known example file."
        )
        lines.append("Ready confirms the connection, not a successful ChatGPT read.")
    else:
        lines.append("Local configuration saved. No ready tunnel is confirmed.")
        if runtime.get("error"):
            lines.append(runtime["error"]["message"])
        lines.append(
            "Next: open one intended folder, or resume it by name. No folders resume automatically."
        )
    return "\n".join(lines)
