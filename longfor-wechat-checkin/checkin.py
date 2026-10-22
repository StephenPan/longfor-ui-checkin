"""Local, fail-closed WeChat check-in. No service API or login-token access."""
import argparse
import hashlib
import json
import math
import msvcrt
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import time
from datetime import datetime, time as daytime, timedelta, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from longfor_coordination import channel_session
BEIJING = timezone(timedelta(hours=8))
STOP_WORDS = ("验证码", "滑块", "安全验证", "操作异常", "访问异常", "请先登录", "立即登录", "授权登录", "手机号登录", "短信验证")
TERMINAL = {"verified", "already_signed", "submission_intent", "uncertain"}
CHALLENGE_WORDS = ("验证码", "滑块", "安全验证", "操作异常", "访问异常", "短信验证")
STARTUP_TIMEOUT_SECONDS = 120


def now():
    return datetime.now(BEIJING)


def in_window(instant):
    current = instant.astimezone(BEIJING).time().replace(tzinfo=None)
    return daytime(6, 0) <= current < daytime(7, 30)


def names(snapshot):
    return [e.get("name", "") for e in snapshot.get("elements", [])]


def ensure_safe(snapshot):
    if login_required(snapshot):
        raise RuntimeError("登录状态已失效，需要恢复原账号后才能签到")
    for name in names(snapshot):
        if any(word in name for word in STOP_WORDS):
            raise RuntimeError("登录、验证或异常页面：需要人工处理，已停止点击")


def ensure_no_challenge(snapshot):
    if any(word in label for label in names(snapshot) for word in CHALLENGE_WORDS):
        raise RuntimeError("出现验证码、安全验证或异常页面，需要人工处理")


def login_required(snapshot):
    return any(marker in label for label in names(snapshot) for marker in ("立即登录", "欢迎登录龙湖", "申请注册并验证你的手机号", "手机号登录", "授权登录", "服务协议及隐私保护", "登录失效", "登录已过期", "请重新登录", "请先登录"))


def login_stage(snapshot):
    labels = names(snapshot)
    if "申请注册并验证你的手机号" in labels:
        return "phone" if any(re.fullmatch(r"1\d{2}\*{4}\d{4}", label) for label in labels) else "loading"
    if "服务协议及隐私保护" in labels:
        return "consent" if "同意" in labels else "loading"
    if "欢迎登录龙湖" in labels:
        return "quick" if "快捷登录" in labels and "我已阅读并同意" in labels else "loading"
    if "立即登录" in labels and "登录查看成长值及会员等级" in labels:
        return "entry"
    if not login_required(snapshot) and "会员" in labels and "首页" in labels:
        return "member"
    return "unknown"


def login_step(snapshot, config):
    ensure_no_challenge(snapshot)
    labels = names(snapshot)
    login = config.get("login", {})
    if not login.get("enabled"):
        raise RuntimeError("重新登录流程尚未配置")
    stage = login_stage(snapshot)
    if stage == "phone":
        candidates = [label for label in labels if re.fullmatch(r"1\d{2}\*{4}\d{4}", label)]
        if len(candidates) != 1 or hashlib.sha256(candidates[0].encode()).hexdigest() != login.get("phone_hash") or "上次登录" not in labels:
            raise RuntimeError("微信授权手机号与已核实号码不一致，停止登录")
        return {"name": candidates[0], "phone_hash": login["phone_hash"]}
    if stage == "consent":
        if not login.get("quick_login_consent"):
            raise RuntimeError("登录协议确认尚未获得用户授权")
        return {"name": "同意", "consent": "true"}
    if stage == "quick":
        return {"name": "快捷登录"}
    if stage == "entry":
        return {"name": "立即登录"}
    if stage == "member":
        return {"name": "会员"}
    raise RuntimeError("登录页面没有已核实的微信快捷登录入口，需要人工处理")


def ensure_logged_in(snapshot, config, scheduled):
    attempted = set()
    attempted_stages = set()
    for _ in range(8):
        ensure_no_challenge(snapshot)
        if not login_required(snapshot) and account_matches(snapshot, config.get("account_hash", "")):
            ensure_safe(snapshot)
            return snapshot
        if not login_required(snapshot) and "我的珑珠" in names(snapshot):
            raise RuntimeError("当前会员账号与已核实账号不一致，停止签到")
        if scheduled and not in_window(now()):
            raise RuntimeError("已到7:30，停止登录和签到")
        stage = login_stage(snapshot)
        log("login_state", stage=stage, login_required=login_required(snapshot), signing=False)
        if stage in ("loading", "unknown"):
            snapshot = wait_for(lambda value: account_matches(value, config.get("account_hash", "")) and not login_required(value) or login_stage(value) not in ("loading", "unknown"), allow_login=True, scheduled=scheduled, phase="login_controls_ready")
            continue
        step = login_step(snapshot, config)
        key = tuple(sorted(step.items()))
        if key in attempted:
            raise RuntimeError("登录步骤未完成，不重复发送登录请求")
        attempted.add(key)
        attempted_stages.add(stage)
        bridge("login-click", **step)
        log("login_step", step="phone_authorize" if "phone_hash" in step else step["name"])
        # Poll page state, not the next action: partial authorization controls
        # are loading states, and an unchanged view never repeats a request.
        snapshot = wait_for(lambda value: (not login_required(value) and (account_matches(value, config.get("account_hash", "")) or "我的珑珠" in names(value))) or login_stage(value) not in attempted_stages | {"loading", "unknown"}, allow_login=True, scheduled=scheduled, phase="login_after_" + stage)
    raise RuntimeError("限定次数内未完成登录，停止签到")


def account_matches(snapshot, expected_hash):
    return any(hashlib.sha256(n.encode("utf-8")).hexdigest() == expected_hash for n in names(snapshot))


def growth_evidence(snapshot):
    labels = names(snapshot)
    # A calendar's future '+5' alone is never proof of today's credit.
    if "每日签到" not in labels or "今日已获得" not in labels:
        return None
    amounts = [n for n in labels if n.endswith("成长值") and n[:-3].isdigit()]
    return {"kind": "成长值", "display": amounts[0]} if amounts else None


def primary_evidence(snapshot, config):
    labels = names(snapshot)
    if not all(marker in labels for marker in config.get("success_markers", [])):
        return None
    if not config.get("success_markers"):
        return None
    if active_page(snapshot) != "每日签到":
        return None
    if config.get("evidence_type") == "growth":
        return growth_evidence(snapshot)
    return {"kind": config["reward_kind"], "display": config.get("reward_display", "页面确认")}


def active_page(snapshot):
    specific = [e.get("name", "") for e in snapshot.get("elements", [])
                if e.get("type") == "ControlType.Document" and e.get("name") not in ("", "AppIndex", "Page-Frame", "Context")]
    return specific[-1] if specific else "main"


def blocks_reentry(state, date):
    return state.get("date") == date and state.get("status") in TERMINAL


def write_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def log(event, **fields):
    (ROOT / "logs").mkdir(exist_ok=True)
    row = {"time": now().isoformat(timespec="seconds"), "event": event, **fields}
    with (ROOT / "logs" / (now().strftime("%Y-%m-%d") + ".jsonl")).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False))


def bridge(action="inspect", **options):
    command = [str(ROOT / "DesktopBridge.exe"), "--action", action]
    for key, value in options.items():
        command.extend(["--" + key.replace("_", "-"), str(value)])
    result = subprocess.run(command, capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        payload = json.loads(result.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("窗口检查程序未返回有效结果，已停止") from error
    if result.returncode != 0 or not payload.get("ok"):
        raise RuntimeError(payload.get("error", "窗口检查失败"))
    return payload


def snapshot_summary(snapshot):
    page = active_page(snapshot)
    return {"element_count": len(snapshot.get("elements", [])),
            "page": page if page in ("main", "每日签到", "珑珠明细") else "other",
            "member_tab": "会员" in names(snapshot), "home_tab": "首页" in names(snapshot),
            "login_page": login_required(snapshot),
            "login_stage": login_stage(snapshot),
            "close_label_count": sum(label in ("关闭", "关闭广告", "关闭活动弹窗") for label in names(snapshot))}


def wait_for(predicate, timeout=35, allow_login=False, scheduled=False, phase="page", startup=False):
    deadline = time.monotonic() + timeout
    snapshot = {}
    focus_failures = 0
    while time.monotonic() < deadline:
        if scheduled and not in_window(now()):
            raise RuntimeError("已到7:30，停止等待登录或签到页面")
        try:
            snapshot = bridge(foreground="true") if startup else bridge()
        except RuntimeError as error:
            # The official shortcut can need more than three seconds to create
            # its window. Other errors, including multiple windows, stay fatal.
            if startup and str(error) == "Target window is not foreground; refusing input":
                focus_failures += 1
                # This was an inspection, before input. Read background state
                # for diagnosis only; never bypass the foreground input guard.
                try:
                    snapshot = bridge()
                    ensure_no_challenge(snapshot)
                except RuntimeError as inspect_error:
                    if str(inspect_error) != "Expected one visible 龙湖天街 WeChatAppEx window; found 0":
                        raise
                    snapshot = {}
                log("startup_focus_retry", attempt=focus_failures, snapshot=snapshot_summary(snapshot), signing=False)
                if focus_failures >= 3:
                    raise RuntimeError("小程序连续3次无法恢复前台，尚未点击登录或签到，请检查窗口遮挡") from error
                time.sleep(0.8)
                continue
            if not startup or str(error) != "Expected one visible 龙湖天街 WeChatAppEx window; found 0":
                raise
            snapshot = {}
            time.sleep(1)
            continue
        if scheduled and not in_window(now()):
            raise RuntimeError("已到7:30，停止等待登录或签到页面")
        (ensure_no_challenge if allow_login else ensure_safe)(snapshot)
        if predicate(snapshot):
            return snapshot
        time.sleep(1)
    log("page_wait_timeout", phase=phase, snapshot=snapshot_summary(snapshot))
    raise RuntimeError("页面在规定时间内未加载为可确认状态（阶段：" + phase + "），已停止")


def sign_card(snapshot):
    if active_page(snapshot) != "main":
        raise RuntimeError("签到卡片必须位于会员主页面")
    elements = snapshot.get("elements", [])
    anchors = [i for i, e in enumerate(elements) if e.get("name") == "赚珑珠"]
    if len(anchors) != 1:
        raise RuntimeError("会员签到栏目不唯一，已停止")
    cards = []
    for element in elements[anchors[0] + 1:]:
        if element.get("name") == "花珑珠":
            break
        if element.get("type") == "ControlType.Image" and element.get("class_name") == "image":
            cards.append(element)
    if len(cards) != 2 or not cards[0].get("automation_id"):
        raise RuntimeError("会员签到卡片布局已变化，需重新核实")
    return cards[0]


def button_score(image, card, window):
    from PIL import Image
    import cv2
    import numpy as np
    left, top = card["x"] - window["x"], card["y"] - window["y"]
    with Image.open(image) as screenshot:
        if screenshot.size != (window["width"], window["height"]) or card["width"] <= 0 or card["height"] <= 0 or left < 0 or top < 0 or left + card["width"] > screenshot.width or top + card["height"] > screenshot.height:
            raise RuntimeError("截图尺寸或签到卡片位置与当前窗口不一致，停止识别")
        normalized = screenshot.crop((left, top, left + card["width"], top + card["height"])).resize((529, 66))
        button = np.array(normalized.crop((428, 0, 529, 66)).convert("L"))
    scores = []
    for template_path in ROOT.glob("sign-button-template*.png"):
        with Image.open(template_path) as template:
            reference = np.array(template.convert("L"))
        scores.append(float(cv2.matchTemplate(button, reference, cv2.TM_CCOEFF_NORMED)[0, 0]))
    return max(scores) if scores else -1.0


def card_visible(snapshot, card):
    window = snapshot["window"]
    tab_tops = [e["y"] for e in snapshot["elements"] if e.get("class_name", "").startswith("tabbar_item ")]
    bottom = min(tab_tops) if tab_tops else window["y"] + window["height"] - 90
    return not card.get("offscreen") and window["y"] + 110 < card["y"] and card["y"] + card["height"] < bottom


def entry_signature(snapshot, card):
    return tuple(snapshot["window"][key] for key in ("x", "y", "width", "height")) + tuple(card.get(key) for key in ("automation_id", "x", "y", "width", "height", "offscreen"))


def prepare_sign_entry(scheduled):
    capture = ROOT / "entry-check.png"
    mismatches = 0
    try:
        for attempt in range(13):
            if scheduled and not in_window(now()):
                raise RuntimeError("已到7:30，停止寻找签到入口")
            snapshot = bridge()
            ensure_safe(snapshot)
            card = sign_card(snapshot)
            window = snapshot["window"]
            top = window["y"] + 110
            if card_visible(snapshot, card):
                captured = bridge(capture=str(capture))
                ensure_safe(captured)
                captured_card = sign_card(captured)
                after = bridge()
                ensure_safe(after)
                after_card = sign_card(after)
                if not card_visible(captured, captured_card) or not card_visible(after, after_card) or not (entry_signature(snapshot, card) == entry_signature(captured, captured_card) == entry_signature(after, after_card)):
                    log("entry_layout_moved", attempt=attempt + 1, signing=False)
                    time.sleep(0.8)
                    continue
                if scheduled and not in_window(now()):
                    raise RuntimeError("已到7:30，停止签到入口核对")
                score = button_score(capture, captured_card, captured["window"])
                log("entry_image_check", score=round(score, 5) if math.isfinite(score) else None, threshold=0.96,
                    card_size=[captured_card["width"], captured_card["height"]], attempt=attempt + 1, signing=False)
                if not math.isfinite(score) or score < 0.96:
                    mismatches += 1
                    if mismatches >= 3:
                        raise RuntimeError("签到按钮连续3次与已核实的‘去签到’不一致，已停止")
                    time.sleep(1)
                    continue
                return after_card
            if attempt == 12:
                break
            bridge("scroll-member", direction="up" if card["y"] <= top else "down")
            time.sleep(0.8)
        raise RuntimeError("限定滚动次数内未找到可见签到入口，已停止")
    finally:
        capture.unlink(missing_ok=True)


def perform(step, scheduled):
    # Closing a verified session is cleanup, even if sign-in completed at 07:30.
    closing = step["action"] == "close-applet"
    if scheduled and not closing and not in_window(now()):
        raise RuntimeError("已超出北京时间6:00—7:30，停止后续点击")
    snapshot = bridge()
    ensure_safe(snapshot)
    marker = step.get("require", "")
    if marker and marker not in names(snapshot):
        raise RuntimeError("当前页面缺少已核实的入口标记，拒绝点击")
    time.sleep(random.uniform(0.4, 1.2))
    if scheduled and not closing and not in_window(now()):
        raise RuntimeError("已到7:30截止时间，停止后续点击")
    if step["action"] in ("prepare-sign-entry", "click-sign-entry"):
        card = prepare_sign_entry(scheduled)
        if step["action"] == "prepare-sign-entry":
            return
        if scheduled and not in_window(now()):
            raise RuntimeError("已到7:30，停止签到点击")
        bridge("click-id", id=card["automation_id"], require="赚珑珠", fx=0.90, fy=0.50)
        return
    result = bridge(step["action"], **{key: value for key, value in step.items() if key not in ("action", "wait_markers", "submits")})
    if closing and not result.get("closed"):
        raise RuntimeError("未确认小程序窗口已退出")
    markers = step.get("wait_markers", [])
    if markers:
        wait_for(lambda value: all(label in names(value) for label in markers))


def finish_success(config, state, scheduled):
    if state.get("date") != now().date().isoformat() or state.get("status") != "verified":
        raise RuntimeError("仅在今日签到成功已核实后执行退出")
    try:
        for step in config.get("return_route", []):
            perform(step, scheduled)
            if step["action"] == "close-applet":
                state["cleanup"] = {"status": "closed", "time": now().isoformat()}
                write_json(ROOT / "state.json", state)
                log("applet_closed")
                with (ROOT / "最新执行结果.txt").open("a", encoding="utf-8") as result:
                    result.write("\n已点击右上角圆点图标，确认小程序已退出。")
    except Exception as error:
        # Cleanup failure must never make a verified reward eligible for resubmission.
        state["cleanup"] = {"status": "failed", "time": now().isoformat(), "error": str(error)}
        write_json(ROOT / "state.json", state)
        log("return_failed", error=str(error), signing_status="verified")
        with (ROOT / "最新执行结果.txt").open("a", encoding="utf-8") as result:
            result.write("\n签到已核实，但退出未确认：" + str(error))


def execute(scheduled=True, navigate_only=False, verify_entry=False):
    config = load_json(ROOT / "config.json", {})
    if not config.get("enabled") and not (navigate_only or verify_entry):
        log("disabled", reason=config.get("pending_reason", "尚未完成真实入口验证"))
        return 3
    if not (navigate_only or verify_entry) and (not config.get("route") or not config.get("success_markers") or not any(step.get("submits") for step in config["route"])):
        raise RuntimeError("实际签到入口、到账标记或提交步骤尚未配置，拒绝执行")
    if scheduled and not in_window(now()):
        log("outside_window")
        return 0
    state_path = ROOT / "state.json"
    state = load_json(state_path, {})
    date = now().date().isoformat()
    if blocks_reentry(state, date) and not (navigate_only or verify_entry):
        log("duplicate_skipped", status=state.get("status"))
        return 0
    # The official shortcut opens only this applet; never type into WeChat chats.
    shortcut = ROOT / config.get("shortcut", "龙湖天街.lnk")
    if not shortcut.is_file():
        raise RuntimeError("官方小程序快捷方式缺失，已停止")
    os.startfile(str(shortcut))
    time.sleep(3)
    snapshot = wait_for(lambda value: "会员" in names(value) or login_required(value) or account_matches(value, config.get("account_hash", "")) or active_page(value) in ("每日签到", "珑珠明细"), timeout=STARTUP_TIMEOUT_SECONDS, allow_login=True, scheduled=scheduled, phase="startup", startup=True)
    if active_page(snapshot) in ("每日签到", "珑珠明细"):
        perform({"action": "click-class", "class": "back", "require": active_page(snapshot)}, scheduled)
        snapshot = wait_for(lambda value: active_page(value) == "main" and ("会员" in names(value) or login_required(value)), allow_login=True, scheduled=scheduled, phase="startup_return")
    if active_page(snapshot) != "main":
        raise RuntimeError("当前打开了未校准的子页面，已停止，需回到首页或会员页")
    snapshot = ensure_logged_in(snapshot, config, scheduled)
    if not account_matches(snapshot, config.get("account_hash", "")):
        raise RuntimeError("当前账号与已校准账号不一致，已停止")
    log("account_verified", channel="小程序")
    if verify_entry:
        prepare_sign_entry(scheduled)
        log("entry_verified", signing=False)
        return 0
    if navigate_only:
        log("navigation_verified", signing=False)
        return 0
    submitted = False
    try:
        for step in config["route"]:
            if step.get("submits"):
                # Persist BEFORE an action that may credit rewards, including opening
                # an auto-sign page. A crash must not lead to another submit today.
                state = {"date": date, "status": "submission_intent", "time": now().isoformat(), "channel": "小程序"}
                write_json(state_path, state)
                submitted = True
            perform(step, scheduled)
        snapshot = wait_for(lambda value: primary_evidence(value, config) is not None, phase="credit_after_submission")
        evidence = primary_evidence(snapshot, config)
        state = {"date": date, "status": "verified", "time": now().isoformat(), "channel": "小程序", "reward": evidence}
        write_json(state_path, state)
        submitted = False
        log("verified", reward=evidence)
        (ROOT / "最新执行结果.txt").write_text(now().strftime("%Y-%m-%d %H:%M:%S") + " 北京时间\n小程序今日签到状态已核实：今日已获得 " + evidence["display"] + "（页面显示类型：" + evidence["kind"] + "）", encoding="utf-8")
        finish_success(config, state, scheduled)
        return 0
    except Exception as error:
        if submitted:
            write_json(state_path, {"date": date, "status": "uncertain", "time": now().isoformat(), "channel": "小程序", "error": str(error)})
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["scheduled", "verify-navigation", "verify-entry", "manual", "status"], default="scheduled", nargs="?")
    arguments = parser.parse_args()
    if arguments.mode == "status":
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
                if arguments.mode == "scheduled" and not in_window(now()):
                    raise RuntimeError("已超出签到时段，停止等待另一渠道")
            if arguments.mode == "scheduled" and not in_window(now()):
                return execute(scheduled=True)
            with channel_session(session_guard):
                return execute(scheduled=arguments.mode == "scheduled", navigate_only=arguments.mode == "verify-navigation", verify_entry=arguments.mode == "verify-entry")
        except Exception as error:
            log("failed", error=str(error), manual_attention=True)
            (ROOT / "最新执行结果.txt").write_text(now().strftime("%Y-%m-%d %H:%M:%S") + " 北京时间\n签到未确认完成：" + str(error), encoding="utf-8")
            return 2
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    sys.exit(main())
