"""Scoped SMS login assistance. Codes remain in memory and never enter logs."""
import argparse
import getpass
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
import sys
import time

from android_bridge import Android, ensure_no_challenge, login_required, select_node
from run_state import ROOT, load_json, now, write_json

PHONE_FILE = ROOT / "login-phone.dpapi"
AUTH_STATE = ROOT / "login-state.json"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt_phone(data, decrypt=False):
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        success = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        success = function(ctypes.byref(source), "Longfor login phone", None, None, None, 1, ctypes.byref(output))
    if not success:
        raise RuntimeError("Windows本机登录资料加密或读取失败")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def phone_matches(value, expected_hash):
    return bool(re.fullmatch(r"1\d{10}", value)) and hashlib.sha256((value[:3] + "****" + value[-4:]).encode()).hexdigest() == expected_hash


def save_phone(value, config):
    if not phone_matches(value, config.get("login", {}).get("phone_hash", "")):
        raise RuntimeError("手机号与已核实的原会员手机号不一致")
    encrypted = crypt_phone(value.encode("ascii"))
    temporary = PHONE_FILE.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, PHONE_FILE)


def load_phone(config):
    if not PHONE_FILE.exists():
        raise RuntimeError("APP短信登录手机号尚未配置，需要用户提供")
    value = crypt_phone(PHONE_FILE.read_bytes(), decrypt=True).decode("ascii")
    if not phone_matches(value, config.get("login", {}).get("phone_hash", "")):
        raise RuntimeError("本机登录手机号与原会员手机号不一致")
    return value


def native_sms_page(nodes, package):
    ids = [node.get("resource-id", "") for node in nodes]
    return all(package + ":id/" + item in ids for item in ("login_root_view", "et_login_mobile", "et_verify_code", "tv_welcome"))


def set_digits(phone, kind, value, guard=lambda: None):
    pattern = r"1\d{10}" if kind == "phone" else r"\d{4,8}" if kind == "code" else r"(?!)"
    if not re.fullmatch(pattern, value):
        raise RuntimeError("登录输入格式不正确")
    nodes = phone.snapshot()
    ensure_no_challenge(nodes)
    if not native_sms_page(nodes, phone.package):
        raise RuntimeError("当前不是已核实的APP短信登录页面")
    field = "et_login_mobile" if kind == "phone" else "et_verify_code"
    node, point = select_node(nodes, {"resource-id": phone.package + ":id/" + field})
    if node.get("class") != "android.widget.EditText":
        raise RuntimeError("登录输入控件类型变化")
    guard()
    phone.require_foreground()
    phone.shell("input", "tap", str(point[0]), str(point[1]))
    guard()
    phone.require_foreground()
    phone.shell("input", "keyevent", "123", *(["67"] * 16))
    guard()
    phone.require_foreground()
    phone.shell("input", "text", value)


def request_sms(phone, config, guard=lambda: None):
    value = load_phone(config)
    nodes = phone.snapshot()
    ensure_no_challenge(nodes)
    if not native_sms_page(nodes, phone.package):
        raise RuntimeError("当前不是APP短信登录页")
    agreement = [node for node in nodes if node.get("resource-id") == phone.package + ":id/login_agree_checked"]
    if len(agreement) != 1 or agreement[0].get("checked") != "true" or not config.get("login", {}).get("app_consent_authorized"):
        raise RuntimeError("登录协议尚未确认或未获授权")
    state = load_json(AUTH_STATE, {})
    if state.get("date") == now().date().isoformat() and state.get("time"):
        raise RuntimeError("今日已请求登录验证码，请提供已有短信验证码，不重复发送")
    set_digits(phone, "phone", value, guard)
    profile = {"selector": {"resource-id": phone.package + ":id/tv_get_code"},
               "required_ids": [phone.package + ":id/login_root_view"],
               "required_checked": {phone.package + ":id/login_agree_checked": "true"}}
    state = {"date": now().date().isoformat(), "status": "request_intent", "time": now().isoformat()}
    guard()
    write_json(AUTH_STATE, state)
    phone.login_tap(profile, guard)
    confirmed = False
    for _ in range(4):
        guard()
        time.sleep(0.5)
        nodes = phone.snapshot()
        ensure_no_challenge(nodes)
        buttons = [node for node in nodes if node.get("resource-id") == phone.package + ":id/tv_get_code"]
        if len(buttons) == 1 and buttons[0].get("enabled") == "false" and re.search(r"\d+", buttons[0].get("text", "")):
            confirmed = True
            break
    if not confirmed:
        raise RuntimeError("已点击验证码请求，但未确认发送状态，请检查短信，不自动重发")
    state["status"] = "code_requested"
    write_json(AUTH_STATE, state)
    return state


def complete_sms(phone, config, code):
    if not re.fullmatch(r"\d{4,8}", code):
        raise RuntimeError("验证码应为4至8位数字")
    state = load_json(AUTH_STATE, {})
    if state.get("date") != now().date().isoformat() or state.get("status") not in ("code_requested", "code_submission_intent"):
        raise RuntimeError("尚未请求当前登录的短信验证码")
    set_digits(phone, "phone", load_phone(config))
    set_digits(phone, "code", code)
    state["status"] = "code_submission_intent"
    state["submit_time"] = now().isoformat()
    write_json(AUTH_STATE, state)
    phone.login_tap({"selector": {"resource-id": phone.package + ":id/tv_login", "text": "登录"},
                     "required_ids": [phone.package + ":id/login_root_view"],
                     "required_checked": {phone.package + ":id/login_agree_checked": "true"}})
    from checkin import account_matches, navigate_member, wait_snapshot
    wait_snapshot(phone, lambda nodes: not login_required(nodes) and "会员" in [node.get("text") for node in nodes], allow_login=True)
    nodes = navigate_member(phone, config, lambda: None)
    if not account_matches(nodes, config):
        raise RuntimeError("登录后会员账号不一致，停止签到")
    state["status"] = "logged_in"
    state["verified_time"] = now().isoformat()
    state["account_verified"] = True
    write_json(AUTH_STATE, state)
    phone.finish_and_sleep()
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["request", "complete"])
    args = parser.parse_args()
    # Sensitive values arrive over stdin, never command-line arguments or files.
    if sys.stdin.isatty():
        payload = {"phone": getpass.getpass("会员登录手机号（隐藏输入，已有加密号码可直接回车）：")} if args.mode == "request" else {"code": getpass.getpass("本次短信验证码（隐藏输入）：")}
    else:
        payload = json.load(sys.stdin)
    config = load_json(ROOT / "config.json", {})
    phone = Android(config)
    sys.path.insert(0, str(ROOT.parent))
    from longfor_coordination import channel_session
    with channel_session():
        if not config.get("login", {}).get("app_consent_authorized"):
            raise RuntimeError("APP登录协议尚未获得用户授权")
        phone.wake()
        if phone.foreground() != phone.package:
            phone.launch()
        for _ in range(3):
            nodes = phone.snapshot()
            agreement = [node for node in nodes if node.get("resource-id") == phone.package + ":id/login_agree_checked"]
            if native_sms_page(nodes, phone.package) and len(agreement) == 1 and agreement[0].get("checked") == "true":
                break
            from android_bridge import choose_login_profile
            profile = choose_login_profile(nodes, config.get("login", {}).get("profiles", []))
            if not profile:
                raise RuntimeError("当前没有已核实的APP短信登录入口")
            phone.login_tap(profile)
        if args.mode == "request":
            if payload.get("phone"):
                save_phone(payload["phone"], config)
            result = request_sms(phone, config)
        else:
            result = complete_sms(phone, config, payload.get("code", ""))
            result["signing"] = resume_signing(config)
    print(json.dumps({"status": result["status"], "signing": result.get("signing", "not_requested")}, ensure_ascii=False))


def resume_signing(config):
    from run_state import blocks_reentry, in_window
    if blocks_reentry(load_json(ROOT / "state.json", {}), now().date().isoformat()):
        return "already_done_or_pending_review"
    if not in_window(now()):
        return "outside_window"
    if not config.get("enabled"):
        return "disabled"
    from checkin import execute
    return "resumed" if execute(scheduled=True) == 0 else "needs_attention"


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "needs_attention", "error": str(error)}, ensure_ascii=False))
        sys.exit(2)
