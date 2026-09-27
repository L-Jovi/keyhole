"""Test release bytes outside the checkout; no account or production state is used."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def run(*args, cwd, env=None):
    clean = {
        k: v
        for k, v in (env or os.environ).items()
        if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    subprocess.run(args, cwd=cwd, env=clean, check=True)


@contextmanager
def temporary_workspace(windows):
    directory = tempfile.TemporaryDirectory(
        prefix="keyhole-artifacts-", dir=Path.cwd() if windows else None
    )
    try:
        yield Path(directory.name).resolve()
    finally:
        for attempt in range(31):
            try:
                directory.cleanup()
                break
            except OSError as exc:
                if (
                    not windows
                    or getattr(exc, "winerror", None) not in (5, 32, 33)
                    or attempt == 30
                ):
                    raise
                # Real Windows executables can retain an image mapping briefly
                # after exit. Never turn a failed fixture cleanup into success.
                time.sleep(0.1)
        assert not Path(directory.name).exists()


def main():
    root = Path(__file__).resolve().parents[2]
    wheels = list((root / "dist").glob("keyhole_mcp-*.whl"))
    sources = list((root / "dist").glob("keyhole_mcp-*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Build into a clean dist directory first."
    uv = shutil.which("uv")
    assert uv
    windows = sys.platform == "win32"
    scripts = "Scripts" if windows else "bin"
    python_name = "python.exe" if windows else "python"
    cli_name = "keyhole.exe" if windows else "keyhole"

    def regression(python, check):
        if windows:
            shutil.copytree(root / "contrib/windows", check / "windows-suite", dirs_exist_ok=True)
            for suite in ("test_windows_files.py", "test_windows_integration.py"):
                run(str(python), str(check / "tests" / suite), "-v", cwd=check)
            run(str(python), str(check / "windows-suite/common_regression.py"), cwd=check)
        else:
            run(str(python), "-m", "pytest", "-q", "--tb=short", cwd=check)

    with temporary_workspace(windows) as work:
        venv = work / "wheel-env"
        run(uv, "venv", "--python", sys.executable, str(venv), cwd=work)
        python = venv / scripts / python_name
        run(
            uv,
            "pip",
            "install",
            "--only-binary",
            ":all:",
            "--python",
            str(python),
            str(wheels[0]),
            "pytest",
            cwd=work,
        )
        check = work / "wheel-tests"
        check.mkdir()
        for name in ("tests", ".codex-plugin"):
            shutil.copytree(root / name, check / name)
        run(str(venv / scripts / cli_name), "--version", cwd=check)
        regression(python, check)
        run(
            str(python),
            "-c",
            "from keyhole.client_install import asset; assert asset()['sha256']",
            cwd=check,
        )

        source = work / "source"
        source.mkdir()
        with tarfile.open(sources[0]) as archive:
            archive.extractall(source, filter="data")
        (unpacked,) = source.iterdir()
        run(uv, "sync", "--locked", "--python", sys.executable, "--group", "dev", cwd=unpacked)
        regression(unpacked / ".venv" / scripts / python_name, unpacked)

        # uv's actual isolated tool installer, without relying on the developer's shell profile.
        env = {
            **os.environ,
            "UV_TOOL_DIR": str(work / "tools"),
            "UV_TOOL_BIN_DIR": str(work / "bin"),
        }
        run(
            uv,
            "tool",
            "install",
            "--no-build",
            "--python",
            sys.executable,
            str(wheels[0]),
            cwd=work,
            env=env,
        )
        env["PATH"] = str(work / "bin") + os.pathsep + os.defpath
        if windows:
            shell = (
                Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            run(
                str(shell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-Command keyhole -ErrorAction Stop | Select-Object -ExpandProperty Name; keyhole --version; exit $LASTEXITCODE",
                cwd=work,
                env=env,
            )
        else:
            run("/bin/sh", "-c", "command -v keyhole && keyhole --version", cwd=work, env=env)
        run(
            "keyhole",
            "--state-dir",
            str(work / "unconfigured"),
            "status",
            "--redact",
            cwd=work,
            env=env,
        )
    print(
        "Wheel, sdist and fresh tool PATH checks passed. This does not verify a ChatGPT connection."
    )
    print(
        json.dumps(
            {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [*wheels, *sources]
            }
        )
    )


if __name__ == "__main__":
    main()
