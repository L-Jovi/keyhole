"""Exercise the real pinned download in disposable state, without a tunnel or API key."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from keyhole.client_install import asset, install
from keyhole.state import StateStore


def main():
    root = Path(
        tempfile.mkdtemp(
            prefix="keyhole-official-client-", dir=Path.cwd() if sys.platform == "win32" else None
        )
    ).resolve()
    try:
        client = install(StateStore(root / "private"), asset())
        result = subprocess.run([client, "--version"], check=True, capture_output=True, text=True)
        print(result.stdout.strip())
        print(
            "Official bundle downloaded, verified and executed; no runtime or tunnel was started."
        )
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    main()
