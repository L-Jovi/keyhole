"""Test release bytes outside the checkout; no account or production state is used."""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def run(*args, cwd, env=None):
    clean = {
        k: v
        for k, v in (env or os.environ).items()
        if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    subprocess.run(args, cwd=cwd, env=clean, check=True)


def main():
    root = Path(__file__).resolve().parents[2]
    wheels = list((root / "dist").glob("keyhole_mcp-*.whl"))
    sources = list((root / "dist").glob("keyhole_mcp-*.tar.gz"))
    assert len(wheels) == len(sources) == 1, "Build into a clean dist directory first."
    uv = shutil.which("uv")
    assert uv
    with tempfile.TemporaryDirectory(prefix="keyhole-artifacts-") as scratch:
        work = Path(scratch).resolve()
        venv = work / "wheel-env"
        run(uv, "venv", "--python", sys.executable, str(venv), cwd=work)
        python = venv / "bin/python"
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
        run(str(venv / "bin/keyhole"), "--version", cwd=check)
        run(str(python), "-m", "pytest", "-q", "--tb=short", cwd=check)
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
        run(uv, "run", "--no-sync", "pytest", "-q", "--tb=short", cwd=unpacked)

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


if __name__ == "__main__":
    main()
