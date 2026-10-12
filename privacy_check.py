"""Inspect actual staged Git blobs. Never print matched sensitive values."""
import io
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PRIVATE_NAMES = {"config.json", "state.json", "login-state.json", "login-phone.dpapi", "task.xml", "validation.json", "connection-status.json", "最新执行结果.txt"}
PATTERNS = {
    "mobile number": r"(?<![\w])1[3-9]\d{9}(?![\w])",
    "masked mobile number": r"1[3-9]\d\*{4}\d{4}",
    "personal Windows path": r"[A-Za-z]:[\\/]Users[\\/][^\s\\/]+",
    "Windows user SID": r"S-1-5-21-[\d-]+",
    "GitHub credential": r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
}


def git(*arguments):
    result = subprocess.run(["git", "-C", str(ROOT), *arguments], capture_output=True, check=True)
    return result.stdout


def check_blob(name, data):
    issues = []
    path = Path(name)
    if path.name in PRIVATE_NAMES or path.suffix.lower() in (".dpapi", ".lnk", ".exe", ".pdb", ".dll", ".zip", ".lock", ".pyc") or any(part in ("logs", "tools", "__pycache__", ".venv") for part in path.parts) or path.name.startswith("maintenance-"):
        return ["private runtime or binary artifact"]
    if path.suffix.lower() == ".png":
        if not path.name.startswith(("sign-button-template", "test-neighbor-button")):
            return ["unreviewed screenshot"]
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.width > 300 or image.height > 200:
                issues.append("image too large for a button template")
            if any(isinstance(value, str) and value for value in image.info.values()):
                issues.append("image textual metadata requires review")
        return issues
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        return ["unreviewed binary file"]
    for label, pattern in PATTERNS.items():
        if re.search(pattern, text):
            issues.append(label)
    if path.name == "config.example.json":
        config = json.loads(text)
        login = config.get("login", {})
        if config.get("enabled") or any(config.get(key) for key in ("device_serial", "account_hash")) or login.get("phone_hash"):
            issues.append("example contains active or personal configuration")
        if login.get("app_consent_authorized") or login.get("quick_login_consent"):
            issues.append("example inherits personal consent")
    return issues


def main():
    rows = [row for row in git("ls-files", "--stage", "-z").decode().split("\0") if row]
    if not rows:
        raise RuntimeError("No staged files; add the intended public files first")
    findings = []
    for row in rows:
        metadata, name = row.split("\t", 1)
        mode, sha, stage = metadata.split()
        if mode != "100644" and mode != "100755" or stage != "0":
            findings.append({"file": name, "issues": ["symlink or unresolved Git entry"]})
            continue
        issues = check_blob(name, git("cat-file", "blob", sha))
        if issues:
            findings.append({"file": name, "issues": issues})
    print(json.dumps({"files_checked": len(rows), "passed": not findings, "findings": findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("Privacy check could not complete; publication must stop.")
        sys.exit(2)
