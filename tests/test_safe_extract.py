import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from tools.safe_extract import extract_zip


class SafeExtractTests(unittest.TestCase):
    def test_extracts_normal_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "package.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("bundle/VERSION.txt", "1")
            out = extract_zip(archive, root / "out")
            self.assertEqual("1", (out / "bundle/VERSION.txt").read_text())

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "package.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                extract_zip(archive, root / "out")
            self.assertFalse((root / "escape.txt").exists())

    def test_rejects_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "package.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr("a.txt", "one")
                    package.writestr("a.txt", "two")
            with self.assertRaises(ValueError):
                extract_zip(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
