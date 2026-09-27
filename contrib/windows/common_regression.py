"""Run shared behavior tests on Windows; POSIX primitives have native counterparts."""

import sys
import unittest
from pathlib import Path

test_root = Path(__file__).parent / "tests"
if not test_root.is_dir():
    test_root = Path.cwd() / "tests"
sys.path.insert(0, str(test_root))

# These methods assert POSIX APIs or metadata, not portable behavior. Their
# native counterparts live in test_windows_files and test_windows_integration.
POSIX_ONLY = {
    "test_traversal_unauthorized_symlinks_hardlinks_and_fifo": "FIFO and POSIX link error semantics",
    "test_symlink_root_and_rename_during_read": "Windows holds ancestor handles against rename",
    "test_private_state_and_text_limits": "POSIX chmod; native ACL cases run separately",
    "test_roots_are_stored_canonically_and_control_paths_are_protected": "macOS volume and root paths",
    "test_text_create_patch_versions_replay_and_preservation": "POSIX executable mode bits",
    "test_links_traversal_protected_controls_and_readonly_mode": "POSIX link error and mode semantics",
    "test_history_is_bounded_private_and_pending_not_purged": "POSIX recovery database mode bits",
    "test_interrupted_exclusive_creation_cleans_only_its_stage": "POSIX hard-link publication; Windows uses exclusive rename",
    "test_process_cleanup_requires_the_exact_module_and_state_option": "POSIX ps/uid; native live-process checks run separately",
    "test_next_command_keeps_custom_paths_and_quotes_them": "POSIX shell hints; Windows uses PowerShell quoting",
}


def selected(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from selected(item)
        else:
            method = item.id().rsplit(".", 1)[-1]
            if method in POSIX_ONLY:
                setattr(item, method, unittest.skip(POSIX_ONLY[method])(getattr(item, method)))
            yield item


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this native suite on Windows.")
    suite = unittest.TestLoader().loadTestsFromNames(
        [
            "test_bridge.BridgeTests",
            "test_readers.ReaderTests",
            "test_writes.WriteTests",
            "test_hardening.BoundaryTests",
            "test_hardening.RecoveryBoundaryTests",
            "test_client_install.ClientInstallTests",
            "test_cli.CliTests",
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected(suite)))
    raise SystemExit(not result.wasSuccessful())


if __name__ == "__main__":
    main()
