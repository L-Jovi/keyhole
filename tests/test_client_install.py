"""Downloaded bytes and archive boundaries are tested before any executable is run."""

import hashlib
import io
import shutil
import stat
import tempfile
import unittest
import urllib.error
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keyhole import client_install
from keyhole.errors import KeyholeError
from keyhole.state import StateStore


class ClientInstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="keyhole-install-")).resolve()
        self.addCleanup(shutil.rmtree, self.root)
        self.store = StateStore(self.root / "Private 状态")
        self.release = client_install.asset()

    def archive(self, extra=None):
        out = io.BytesIO()
        stem = self.release["name"].removesuffix(".zip")
        with zipfile.ZipFile(out, "w") as bundle:
            for name in (
                "tunnel-client",
                "cloudflared",
                "cloudflared-manifest.json",
                "LICENSE",
                "NOTICE",
                f"{stem}-licenses.txt",
                f"{stem}.spdx.json",
            ):
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                bundle.writestr(info, "synthetic file")
            if extra is not None:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    bundle.writestr(*extra)
        blob = out.getvalue()
        self.release.update(size=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        return blob

    def install_with(self, data):
        opener = patch("urllib.request.build_opener")
        mocked = opener.start()
        self.addCleanup(opener.stop)
        mocked.return_value.open.side_effect = lambda *a, **k: io.BytesIO(data)
        return client_install.install(self.store, self.release)

    def test_complete_bundle_atomic_install_and_retry_after_cancelled_setup(self):
        blob = self.archive()
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="0.0.14\n")):
            client = Path(self.install_with(blob))
            inode = client.stat().st_ino
            self.assertEqual(self.install_with(blob), str(client))
            self.assertEqual(client.stat().st_ino, inode)
        self.assertEqual(
            set(p.name for p in client.parent.iterdir()),
            {
                "tunnel-client",
                "cloudflared",
                "cloudflared-manifest.json",
                "LICENSE",
                "NOTICE",
                self.release["name"].removesuffix(".zip") + "-licenses.txt",
                self.release["name"].removesuffix(".zip") + ".spdx.json",
            },
        )
        self.assertEqual(stat.S_IMODE(client.stat().st_mode), 0o700)
        self.assertEqual(list(client.parent.parent.glob(".install-*")), [])

    def test_corrupt_truncated_or_oversize_download_never_executes(self):
        blob = self.archive()
        for bad in (blob[:-1], blob + b"extra", bytes(len(blob))):
            with self.subTest(size=len(bad)), patch("subprocess.run") as execute:
                with self.assertRaises(KeyholeError):
                    self.install_with(bad)
                execute.assert_not_called()
                self.assertFalse(client_install.destination(self.store, self.release).exists())

    def test_archive_traversal_link_and_duplicate_are_rejected(self):
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in (
            ("../escape", "bad"),
            ("/absolute", "bad"),
            (link, "target"),
            ("cloudflared", "duplicate"),
        ):
            with self.subTest(extra=extra[0]), patch("subprocess.run") as execute:
                blob = self.archive(extra)
                with self.assertRaises(KeyholeError) as caught:
                    self.install_with(blob)
                self.assertEqual(caught.exception.code, "client_archive_invalid")
                execute.assert_not_called()
                self.assertFalse((self.root / "escape").exists())

    def test_wrong_version_or_disk_failure_keeps_previous_client(self):
        previous = self.root / "old-client"
        previous.write_bytes(b"old working client")
        blob = self.archive()
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="9.9.9")):
            with self.assertRaises(KeyholeError) as caught:
                self.install_with(blob)
            self.assertEqual(caught.exception.code, "client_verification_failed")
        with (
            patch("keyhole.client_install.extract", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.install_with(blob)
        self.assertEqual(previous.read_bytes(), b"old working client")
        parent = client_install.destination(self.store, self.release).parent
        self.assertEqual(list(parent.glob(".install-*")), [])

    def test_network_failure_has_no_server_error_body(self):
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.URLError("PRIVATE-MARKER")
            with self.assertRaises(KeyholeError) as caught:
                client_install.download(self.release, io.BytesIO())
        self.assertEqual(caught.exception.code, "client_download_failed")
        self.assertNotIn("PRIVATE-MARKER", caught.exception.message)

    def test_keyboard_interrupt_removes_only_its_staging_directory(self):
        blob = self.archive()
        previous = self.root / "previous"
        previous.write_text("keep")
        with (
            patch("subprocess.run", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.install_with(blob)
        parent = client_install.destination(self.store, self.release).parent
        self.assertEqual(list(parent.glob(".install-*")), [])
        self.assertEqual(previous.read_text(), "keep")

    def test_damaged_existing_installation_and_symlink_are_not_replaced(self):
        blob = self.archive()
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="0.0.14")):
            client = Path(self.install_with(blob))
            client.write_text("changed")
            with self.assertRaises(KeyholeError) as caught:
                self.install_with(blob)
            self.assertEqual(caught.exception.code, "client_install_damaged")
            self.assertEqual(client.read_text(), "changed")
            client.unlink()
            outside = self.root / "outside"
            outside.write_text("untouched")
            client.symlink_to(outside)
            with self.assertRaises(OSError):
                self.install_with(blob)
            self.assertEqual(outside.read_text(), "untouched")

    def test_no_https_downgrade(self):
        with self.assertRaises(KeyholeError) as caught:
            client_install.HTTPSOnly().redirect_request(
                None, None, 302, "redirect", {}, "http://example.com"
            )
        self.assertEqual(caught.exception.code, "client_download_failed")

    def test_automatic_download_must_be_a_tested_version(self):
        with (
            patch.dict(client_install.MANIFEST, version="99.0.0"),
            self.assertRaises(KeyholeError) as caught,
        ):
            client_install.asset()
        self.assertEqual(caught.exception.code, "client_download_unavailable")


if __name__ == "__main__":
    unittest.main()
