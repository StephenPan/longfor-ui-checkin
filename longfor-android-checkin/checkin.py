"""Daily UI check-in through the user's authorized Android USB connection."""
import argparse
import hashlib
import json
import msvcrt
import os
import re
import sys
import time

from android_bridge import Android, ensure_safe, ensure_no_challenge, login_required, choose_login_profile, labels
from run_state import ROOT, blocks_reentry, in_window, load_json, mark_submission, now, write_json
sys.path.insert(0, str(ROOT.parent))
from longfor_coordination import channel_session


def log(event, **fields):
    instant = now()
    directory = ROOT / "logs"
    directory.mkdir(exist_ok=True)
    row = {"time": instant.isoformat(timespec="seconds"), "event": event, **fields}
    encoded = json.dumps(row, ensure_ascii=False)
    with (directory / (instant.date().isoformat() + ".jsonl")).open("a", encoding="utf-8") as stream:
        stream.write(encoded + "\n")
    print(encoded)


def account_matches(nodes, config):
    names = [node.get("text", "") for node in nodes
             if node.get("resource-id") == config["package"] + ":id/tv_nickname"]
    return len(names) == 1 and hashlib.sha256(names[0].encode("utf-8")).hexdigest() == config.get("account_hash")


def member_growth(nodes, config):
    values = [node.get("text", "") for node in nodes
              if node.get("resource-id") == config["package"] + ":id/tv_growth_value"]
    return int(values[0]) if len(values) == 1 and values[0].isdigit() else None


def primary_evidence(nodes, config):
    ensure_safe(nodes)
    markers = config.get("success_markers", [])
    values = labels(nodes)
    if not markers or not all(marker in values for marker in markers):
        return None
    headers = [node for node in nodes if node.get("resource-id") == config["package"] + ":id/id_browser_header_title"]
    if len(headers) != 1 or headers[0].get("text") != config.get("sign_page_title"):
        return None
    text = [node.get("text", "") for node in nodes if node.get("text") and node.get("class") != "android.widget.Image"]
    positions = [index for index, value in enumerate(text) if value == "今日已获得"]
    if len(positions) != 1:
        return None
    rewards = []
    # Only values directly following today's credit heading count. Future
    # calendar rewards such as '+ 5' never constitute proof of credit.
    for value in text[positions[0] + 1:positions[0] + 4]:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(成长值|珑珠)", value)
        if not match:
            break
        rewards.append({"kind": match[2], "display": value})
    return {"items": rewards, "display": "、".join(item["display"] for item in rewards)} if rewards else None


def wait_snapshot(phone, predicate, guard=lambda: None, timeout=35, allow_login=False):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        guard()
        try:
            nodes = phone.snapshot()
        except RuntimeError as error:
            # UIAutomator can briefly fail while the WebView animates. Retrying
            # a read must never repeat the sign-in tap.
            if str(error) != "手机连接或操作失败，已停止":
                raise
            time.sleep(1)
            continue
        (ensure_no_challenge if allow_login else ensure_safe)(nodes)
        if predicate(nodes):
            return nodes
        time.sleep(1)
    raise RuntimeError("页面在限定时间内未显示可确认结果，需要人工检查")


def restore_login(phone, config, nodes, guard):
    attempted = set()
    for _ in range(5):
        ensure_no_challenge(nodes)
        if not login_required(nodes):
            return nodes
        profile = choose_login_profile(nodes, config.get("login", {}).get("profiles", []))
        if not profile:
            if config.get("login", {}).get("method") == "sms":
                from login_sms import native_sms_page, request_sms
                if native_sms_page(nodes, phone.package):
                    request_sms(phone, config, guard)
                    log("login_code_requested", channel="APP", manual_attention=True)
                    raise RuntimeError("APP已请求短信验证码，请使用login_sms.py complete输入短信验证码完成登录，尚未执行签到")
            log("login_required", channel="APP", manual_attention=True)
            raise RuntimeError("APP已掉线，需要在手机完成登录；若要求短信验证码，请人工输入后再运行")
        key = json.dumps(profile, sort_keys=True)
        if profile.get("consent_required") and not config.get("login", {}).get("app_consent_authorized"):
            raise RuntimeError("APP登录协议确认未获用户授权")
        if key in attempted:
            raise RuntimeError("登录步骤未完成，不重复发送登录请求")
        attempted.add(key)
        phone.login_tap(profile, guard)
        log("login_step", channel="APP", step=profile.get("name", "已核实登录入口"))
        nodes = wait_snapshot(phone, lambda value: not login_required(value) or choose_login_profile(value, config.get("login", {}).get("profiles", [])) != profile, guard, allow_login=True)
    raise RuntimeError("限定次数内未完成APP登录")


def navigate_member(phone, config, guard):
    nodes = wait_snapshot(phone, lambda value: "会员" in labels(value) or login_required(value), guard, allow_login=True)
    nodes = restore_login(phone, config, nodes, guard)
    ids = [node.get("resource-id", "") for node in nodes]
    if phone.package + ":id/img_market" in ids:
        phone.tap({"resource-id": phone.package + ":id/img_close"},
                  required_ids=(phone.package + ":id/img_market",), time_guard=guard)
        nodes = wait_snapshot(phone, lambda value: "会员" in labels(value) or login_required(value), guard, allow_login=True)
        nodes = restore_login(phone, config, nodes, guard)
    if not account_matches(nodes, config):
        phone.tap({"text": "会员", "resource-id": phone.package + ":id/tab_text"},
                  required_labels=("首页", "会员"), time_guard=guard)
        nodes = wait_snapshot(phone, lambda value: account_matches(value, config) or login_required(value), guard, allow_login=True)
        nodes = restore_login(phone, config, nodes, guard)
    if "我的珑珠" not in labels(nodes) or not account_matches(nodes, config):
        raise RuntimeError("会员账号或页面无法核实，停止签到")
    return nodes


def finish_success(phone, config, state):
    if state.get("date") != now().date().isoformat() or state.get("status") != "verified":
        raise RuntimeError("仅在今日APP签到成功已核实后退出和熄屏")
    try:
        nodes = phone.snapshot()
        ensure_safe(nodes)
        if primary_evidence(nodes, config):
            phone.require_foreground()
            phone.shell("input", "keyevent", "4")
            nodes = wait_snapshot(phone, lambda value: account_matches(value, config))
        if not account_matches(nodes, config):
            raise RuntimeError("未能返回已校准会员页，停止退出操作")
        state["growth_after"] = member_growth(nodes, config)
        write_json(ROOT / "state.json", state)
        phone.finish_and_sleep()
        state["cleanup"] = {"status": "screen_off", "time": now().isoformat()}
        log("screen_off_confirmed")
    except Exception as error:
        state["cleanup"] = {"status": "failed", "time": now().isoformat(), "error": str(error)}
        log("cleanup_failed", error=str(error), signing_status="verified")
    write_json(ROOT / "state.json", state)
    outcome = state.get("cleanup", {}).get("status")
    (ROOT / "最新执行结果.txt").write_text(
        now().strftime("%Y-%m-%d %H:%M:%S") + " 北京时间\nAPP今日签到已核实：" +
        state["reward"]["display"] + ("\n已退出APP页面并确认手机熄屏。" if outcome == "screen_off" else "\n退出或熄屏未确认，请检查手机。"), encoding="utf-8")


def execute(scheduled=True, navigate_only=False):
    config = load_json(ROOT / "config.json", {})
    if scheduled and not in_window(now()):
        log("outside_window")
        return 0
    if not config.get("enabled") and not navigate_only:
        log("disabled", reason=config.get("pending_reason", "尚未完成真实签到验证"))
        return 3
    if not config.get("success_markers") or not config.get("account_hash") or config.get("wake_mode") != "unprotected":
        raise RuntimeError("实际页面或无密码唤醒方式尚未核实")
    date = now().date().isoformat()
    if blocks_reentry(load_json(ROOT / "state.json", {}), date) and not navigate_only:
        log("duplicate_skipped")
        return 0

    def guard():
        if now().date().isoformat() != date or (scheduled and not in_window(now())):
            raise RuntimeError("已超出北京时间6:00—7:30，停止后续签到操作")

    phone = Android(config)
    phone.launch(guard)
    nodes = navigate_member(phone, config, guard)
    before = member_growth(nodes, config)
    log("account_verified", channel="APP")
    if navigate_only:
        phone.finish_and_sleep()
        log("navigation_and_sleep_verified", signing=False)
        return 0
    submitted = False

    def intent():
        nonlocal submitted
        mark_submission()
        submitted = True

    try:
        phone.tap_sign_card(config["account_hash"], guard, before_submit=intent)
        nodes = wait_snapshot(phone, lambda value: primary_evidence(value, config) is not None, guard)
        state = {"date": date, "status": "verified", "time": now().isoformat(), "channel": "APP",
                 "reward": primary_evidence(nodes, config), "growth_before": before}
        write_json(ROOT / "state.json", state)
        submitted = False
        log("verified", reward=state["reward"])
        finish_success(phone, config, state)
        return 0
    except Exception as error:
        if submitted:
            write_json(ROOT / "state.json", {"date": date, "status": "uncertain", "time": now().isoformat(), "channel": "APP", "error": str(error)})
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["scheduled", "manual", "verify-navigation", "status"], nargs="?", default="scheduled")
    args = parser.parse_args()
    if args.mode == "status":
        print(json.dumps(load_json(ROOT / "state.json", {}), ensure_ascii=False, indent=2))
        return 0
    with (ROOT / "run.lock").open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            log("concurrent_run_skipped")
            return 0
        try:
            def session_guard():
                if args.mode == "scheduled" and not in_window(now()):
                    raise RuntimeError("已超出签到时段，停止等待另一渠道")
            if args.mode == "scheduled" and not in_window(now()):
                return execute(scheduled=True)
            with channel_session(session_guard):
                return execute(scheduled=args.mode == "scheduled", navigate_only=args.mode == "verify-navigation")
        except Exception as error:
            log("failed", error=str(error), manual_attention=True)
            (ROOT / "最新执行结果.txt").write_text(now().strftime("%Y-%m-%d %H:%M:%S") + " 北京时间\nAPP自动签到未确认完成：" + str(error), encoding="utf-8")
            return 2
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    sys.exit(main())
