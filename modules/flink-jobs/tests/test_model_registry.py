import hashlib
import io
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_registry import PACKAGE_FILES, verify_package


def archive(entries=None, payload=b"verified"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(PACKAGE_FILES) if entries is None else entries:
            bundle.writestr(name, payload)
    return output.getvalue()


class PackageTests(unittest.TestCase):
    def test_exact_package_and_digest_are_required(self):
        data = archive()
        checksum = hashlib.sha256(data).hexdigest()
        verify_package(data, checksum)
        for digest in ("", "0" * 64, checksum.upper()):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError, "checksum"):
                verify_package(data, digest)

    def test_path_traversal_missing_and_duplicate_entries_are_rejected(self):
        for entries in (
            ["../features.py"], sorted(PACKAGE_FILES)[:-1],
            [*sorted(PACKAGE_FILES), "payment_risk/extra.py"],
        ):
            data = archive(entries)
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, "entries"):
                verify_package(data, hashlib.sha256(data).hexdigest())

    def test_zip_bomb_budget_is_checked_before_model_import(self):
        data = archive(payload=b"x" * 1_500_000)
        with self.assertRaisesRegex(ValueError, "size budget"):
            verify_package(data, hashlib.sha256(data).hexdigest())
