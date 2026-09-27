"""Compare PowerShell startup environments on disposable Windows CI runners."""

import json
import os
import subprocess
import time
from pathlib import Path

from mcp.client.stdio import get_default_environment

from keyhole.errors import KeyholeError
from keyhole.windows_process import query


def main():
    try:
        query(
            "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks | ConvertTo-Json"
        )
        print(json.dumps({"case": "keyhole-query", "status": "success"}), flush=True)
    except KeyholeError as exc:
        print(
            json.dumps(
                {"case": "keyhole-query", "error": str(getattr(exc.__cause__, "stderr", ""))}
            ),
            flush=True,
        )
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = (
        "$ErrorActionPreference='Stop'; [Console]::WriteLine('STARTED'); "
        "Import-Module CimCmdlets; [Console]::WriteLine('IMPORTED'); "
        "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().Ticks"
    )
    minimal = get_default_environment()
    cases = {
        "mcp-with-system-modules": {**minimal, "PSMODULEPATH": str(executable.parent / "Modules")},
        "inherited": dict(os.environ),
        "mcp-default": minimal,
        "mcp-with-windir": {**minimal, "WINDIR": os.environ["SYSTEMROOT"]},
        "mcp-with-module-path": {**minimal, "PSMODULEPATH": os.environ.get("PSMODULEPATH", "")},
    }
    command = (
        "Get-Module -ListAvailable CimCmdlets,Microsoft.PowerShell.Utility | Select-Object Name,Path | ConvertTo-Json; "
        + command
    )
    for name, env in cases.items():
        start = time.monotonic()
        try:
            result = subprocess.run(
                [str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            output = result.stdout
            status = result.returncode
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            status = "timeout"
        # The script prints only fixed markers and a boot timestamp, never env values.
        print(
            json.dumps(
                {
                    "case": name,
                    "status": status,
                    "seconds": round(time.monotonic() - start, 2),
                    "output": output,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
