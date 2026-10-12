import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import privacy_check


class PublicationPrivacy(unittest.TestCase):
    def test_real_looking_values_and_active_examples_are_rejected(self):
        synthetic_number = b"1" + b"39" + b"0" * 8
        self.assertIn("mobile number", privacy_check.check_blob("fixture.py", synthetic_number))
        synthetic_email = b"fixture" + b"@" + b"example.invalid"
        self.assertIn("email", privacy_check.check_blob("fixture.py", synthetic_email))
        value = json.dumps({"enabled": True, "account_hash": "test", "login": {}}).encode()
        self.assertIn("example contains active or personal configuration", privacy_check.check_blob("config.example.json", value))

    def test_private_runtime_files_are_rejected_even_if_encrypted(self):
        for name in ("config.json", "login-phone.dpapi", "logs/day.jsonl", "app-member.png", "task.xml"):
            self.assertTrue(privacy_check.check_blob(name, b"fixture"))

    def test_staged_secret_is_detected_after_working_copy_was_cleaned(self):
        with tempfile.TemporaryDirectory(prefix="publication-privacy-test-") as directory:
            root = Path(directory)
            self.assertEqual(root.resolve().parent, Path(tempfile.gettempdir()).resolve())
            subprocess.run(["git", "-C", directory, "init", "--quiet"], check=True, capture_output=True)
            path = root / "fixture.py"
            synthetic_number = "1" + "39" + "0" * 8
            path.write_text(synthetic_number, encoding="utf-8")
            subprocess.run(["git", "-C", directory, "add", "fixture.py"], check=True, capture_output=True)
            path.write_text("clean working copy", encoding="utf-8")
            output = io.StringIO()
            with patch.object(privacy_check, "ROOT", root), contextlib.redirect_stdout(output):
                self.assertEqual(privacy_check.main(), 1)
            self.assertNotIn(synthetic_number, output.getvalue())
            self.assertIn("mobile number", output.getvalue())


if __name__ == "__main__":
    unittest.main()
