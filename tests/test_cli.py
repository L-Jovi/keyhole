"""CLI behavior that does not need a tunnel: platform guard and argument handling."""

import json
import subprocess
import sys
import textwrap
import unittest

from keyhole import __version__

# Stand in for a Windows interpreter on this machine: no fcntl/resource, no POSIX open flags.
WINDOWS = textwrap.dedent("""
    import argparse, getpass, json, os, pathlib, shutil, subprocess, sys  # real Windows has these
    sys.modules["fcntl"] = None
    sys.modules["resource"] = None
    for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
        if hasattr(os, name):
            delattr(os, name)
    sys.platform = "win32"
    from keyhole import cli
    cli.main(sys.argv[1:])
""")


def run_as_windows(*args):
    return subprocess.run(
        [sys.executable, "-c", WINDOWS, *args], capture_output=True, text=True, timeout=60
    )


class CliTests(unittest.TestCase):
    def test_windows_gets_one_clear_error_instead_of_a_traceback(self):
        for command in (["status"], ["open", "C:/Users/me/project"], ["setup"]):
            result = run_as_windows(*command)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error"]["code"], "unsupported_platform")
            self.assertIn("Windows is not supported", payload["error"]["message"])

    def test_help_and_version_work_everywhere(self):
        result = run_as_windows("--version")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, f"keyhole {__version__}"))
        result = run_as_windows("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("keyhole", result.stdout)


if __name__ == "__main__":
    unittest.main()
