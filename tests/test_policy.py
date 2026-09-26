import unicodedata
import unittest
from pathlib import Path

from keyhole.errors import KeyholeError
from keyhole.policy import check_root, excluded, fold, normalize_pattern


class PolicyTests(unittest.TestCase):
    def test_fold_unifies_case_and_normalization(self):
        self.assertEqual(fold("Café"), fold(unicodedata.normalize("NFD", "CAFÉ")))
        self.assertEqual(fold("Straße"), "strasse")

    def test_normalize_pattern_strips_decoration_and_rejects_escapes(self):
        self.assertEqual(normalize_pattern("Private/"), "Private")
        self.assertEqual(normalize_pattern("/Private/sub/"), "Private/sub")
        self.assertEqual(normalize_pattern("./docs/*.md"), "docs/*.md")
        for bad in ("", "/", "..", "a/../b", "a\\b", "a\nb", "x" * 201):
            with self.assertRaises(KeyholeError) as caught:
                normalize_pattern(bad)
            self.assertEqual(caught.exception.code, "invalid_pattern")

    def test_custom_patterns_match_names_at_any_depth_and_anchored_prefixes(self):
        cases = [
            (("Private", "secret.txt"), ("Private",), True),
            (("private", "secret.txt"), ("Private",), True),
            (("x", "PRIVATE", "y"), ("Private",), True),
            (("Privateer", "a"), ("Private",), False),
            (("Secret", "deep", "key.txt"), ("Secret/*",), True),
            (("secret", "a", "b"), ("Secret/*",), True),
            (("other", "Secret", "a"), ("Secret/*",), False),
            (("docs", "a.md"), ("docs/*.md",), True),
            (("docs", "sub", "b.md"), ("docs/*.md",), True),
            (("docs", "a.txt"), ("docs/*.md",), False),
            (("b.LOG",), ("*.log",), True),
            ((unicodedata.normalize("NFD", "Café"), "x.txt"), ("Café",), True),
            (("notes.txt",), ("Private", "Secret/*"), False),
        ]
        for parts, extra, expected in cases:
            with self.subTest(parts=parts, extra=extra):
                self.assertEqual(excluded(parts, extra), expected)

    def test_builtin_names_match_at_any_depth_ignoring_case(self):
        self.assertTrue(excluded(("src", ".GIT", "config")))
        self.assertTrue(excluded(("a", "node_modules", "b")))
        self.assertTrue(excluded(("keys", "server.PEM")))
        self.assertTrue(excluded((".env.local",)))
        self.assertFalse(excluded(("src", "main.py")))

    def test_check_root_refuses_home_system_and_shallow_paths(self):
        for path in (
            Path.home(),
            Path("/"),
            Path("/Users"),
            Path("/System/Volumes/Data"),
            Path("/System/Volumes/Data/Users/x/project"),
        ):
            with self.assertRaises(KeyholeError) as caught:
                check_root(path)
            self.assertEqual(caught.exception.code, "protected_root")
        with self.assertRaises(KeyholeError) as caught:
            check_root(Path("/Users/x/.ssh/keys"))
        self.assertEqual(caught.exception.code, "excluded_root")
        with self.assertRaises(KeyholeError) as caught:
            check_root(Path("relative/path"))
        self.assertEqual(caught.exception.code, "invalid_root")
        check_root(Path("/Users/x/project"))


if __name__ == "__main__":
    unittest.main()
