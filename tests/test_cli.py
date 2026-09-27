"""CLI behavior that does not need a tunnel: platform guard and argument handling."""

import json
import subprocess
import sys
import textwrap
import unittest

from keyhole import __version__

# Unsupported compatibility layers must fail before importing platform-specific file APIs.
UNSUPPORTED = textwrap.dedent("""
    import argparse, getpass, json, os, pathlib, shutil, subprocess, sys  # stdlib only
    sys.modules["fcntl"] = None
    sys.modules["resource"] = None
    for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
        if hasattr(os, name):
            delattr(os, name)
    sys.platform = "cygwin"
    from keyhole import cli
    cli.main(sys.argv[1:])
""")


def run_as_unsupported(*args):
    return subprocess.run(
        [sys.executable, "-c", UNSUPPORTED, *args], capture_output=True, text=True, timeout=60
    )


class CliTests(unittest.TestCase):
    def test_unsupported_layer_gets_one_clear_error_instead_of_a_traceback(self):
        for command in (["status"], ["open", "C:/Users/me/project"], ["setup"]):
            result = run_as_unsupported(*command)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error"]["code"], "unsupported_platform")
            self.assertIn("compatibility layer is unsupported", payload["error"]["message"])

    def test_help_and_version_work_everywhere(self):
        result = run_as_unsupported("--version")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, f"keyhole {__version__}"))
        result = run_as_unsupported("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("keyhole", result.stdout)


if __name__ == "__main__":
    unittest.main()
