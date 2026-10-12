"""Create disabled, private local configurations; no UI operations or SMS requests."""
import getpass
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
PROJECTS = ("longfor-android-checkin", "longfor-wechat-checkin")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    for project in PROJECTS:
        target = ROOT / project / "config.json"
        if target.exists():
            raise RuntimeError("已有本地配置，向导拒绝覆盖。请手动修改或先备份。")
    nickname = getpass.getpass("龙湖会员昵称（隐藏输入，请与页面完全一致）：")
    phone = getpass.getpass("龙湖会员手机号（隐藏输入）：")
    if not nickname or not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise RuntimeError("请输入昵称和有效的11位中国手机号码")
    account_hash = hashlib.sha256(nickname.encode()).hexdigest()
    phone_hash = hashlib.sha256((phone[:3] + "****" + phone[-4:]).encode()).hexdigest()
    mini_consent = input("已阅读小程序服务协议及隐私政策，并允许以后同类登录自动确认？仅输入 yes 表示允许：").strip().lower() == "yes"
    app_consent = input("已阅读APP服务协议及隐私政策，并允许以后同类登录自动勾选？仅输入 yes 表示允许：").strip().lower() == "yes"
    configs = {}
    for project in PROJECTS:
        config = json.loads((ROOT / project / "config.example.json").read_text(encoding="utf-8"))
        config["account_hash"] = account_hash
        config["login"]["phone_hash"] = phone_hash
        config["enabled"] = False
        if project == "longfor-android-checkin":
            config["login"]["app_consent_authorized"] = app_consent
        else:
            config["login"]["quick_login_consent"] = mini_consent
        configs[project] = config
    sys.path.insert(0, str(ROOT / "longfor-android-checkin"))
    from login_sms import save_phone
    save_phone(phone, configs["longfor-android-checkin"])
    for project, config in configs.items():
        save(ROOT / project / "config.json", config)
    print("本地配置已生成，手机号已用Windows用户加密保存。两套任务均未启用。请绑定设备并完成实际界面验证。")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("配置未完成：" + str(error))
        sys.exit(2)
