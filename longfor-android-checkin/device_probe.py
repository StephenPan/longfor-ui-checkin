"""Read-only Android connection and Longfor package check. No account access."""
import json
from pathlib import Path
import subprocess
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent
ADB = ROOT / "tools" / "platform-tools" / "adb.exe"


def adb(*arguments):
    result = subprocess.run([str(ADB), *arguments], capture_output=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout.decode("utf-8", errors="replace").strip()


def probe():
    rows = [line.split() for line in adb("devices").splitlines()[1:] if line.strip()]
    states = [row[1] for row in rows if len(row) >= 2]
    report = {"checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
              "attached_count": len(states), "connection_states": states,
              "ready": False}
    if len(rows) != 1 or states != ["device"]:
        report["next_step"] = "手机开启USB调试并允许此电脑；保持仅目标手机连接"
        return report
    serial = rows[0][0]
    def shell(*command):
        return adb("-s", serial, "shell", *command)
    report.update(model=shell("getprop", "ro.product.model"),
                  manufacturer=shell("getprop", "ro.product.manufacturer"),
                  android_version=shell("getprop", "ro.build.version.release"))
    packages = set()
    for query in ("longfor", "longhu"):
        packages.update(line.removeprefix("package:") for line in
                        shell("pm", "list", "packages", query).splitlines()
                        if line.startswith("package:"))
    report.update(ready=True, longfor_package_candidates=sorted(packages),
                  next_step="确认龙湖APP安装及登录后，校准实际签到页面")
    return report


if __name__ == "__main__":
    try:
        report = probe()
    except Exception as error:
        report = {"ready": False, "error": str(error)}
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (ROOT / "connection-status.json").write_text(encoded, encoding="utf-8")
    print(encoded)
