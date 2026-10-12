"""ADB page operations restricted to the configured Longfor app and phone."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
ADB = ROOT / "tools" / "platform-tools" / "adb.exe"
REMOTE_VIEW = "/sdcard/longfor_checkin_view.xml"
STOP_WORDS = ("验证码", "滑块", "安全验证", "操作异常", "访问异常", "请先登录", "立即登录", "授权登录", "短信验证")
CHALLENGE_WORDS = ("滑块", "安全验证", "人机验证", "图片验证码", "操作异常", "访问异常")


def parse_nodes(xml, package):
    root = ET.fromstring(xml)
    nodes = []
    for node in root.iter("node"):
        if node.get("package") != package:
            continue
        attributes = dict(node.attrib)
        if attributes.get("password") == "true" or "EditText" in attributes.get("class", ""):
            attributes["text"] = "[输入框已隐藏]"
            attributes["content-desc"] = ""
        for field in ("text", "content-desc"):
            attributes[field] = re.sub(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)", r"\1****\2", attributes.get(field, ""))
        nodes.append(attributes)
    return nodes


def labels(nodes):
    return [node.get(field, "") for node in nodes for field in ("text", "content-desc") if node.get(field)]


def ensure_safe(nodes):
    login_ids = (":id/login_root_view", ":id/et_verify_code")
    if any(node.get("resource-id", "").endswith(login_ids) for node in nodes) or any(word in label for label in labels(nodes) for word in STOP_WORDS):
        raise RuntimeError("登录、验证或异常页面，需要人工处理")


def ensure_no_challenge(nodes):
    if any(word in label for label in labels(nodes) for word in CHALLENGE_WORDS):
        raise RuntimeError("出现安全验证或异常页面，需要人工处理")


def login_required(nodes):
    suffixes = (":id/login_root_view", ":id/et_verify_code")
    markers = ("手机号快捷登录", "请先登录", "立即登录", "授权登录", "登录已过期", "登录失效", "请重新登录")
    return any(node.get("resource-id", "").endswith(suffixes) for node in nodes) or any(marker in label for label in labels(nodes) for marker in markers)


def choose_login_profile(nodes, profiles):
    ensure_no_challenge(nodes)
    ids = [node.get("resource-id", "") for node in nodes]
    matched = []
    for profile in profiles:
        required_ids = profile.get("required_ids", [])
        required_labels = profile.get("required_labels", [])
        if not (required_ids or required_labels):
            continue
        states_match = True
        for resource_id, expected in profile.get("required_checked", {}).items():
            controls = [node for node in nodes if node.get("resource-id") == resource_id]
            if len(controls) != 1 or controls[0].get("checked") != expected:
                states_match = False
                break
        if states_match and all(value in ids for value in required_ids) and all(value in labels(nodes) for value in required_labels):
            select_node(nodes, profile.get("selector", {}))
            matched.append(profile)
    if len(matched) > 1:
        raise RuntimeError("登录入口不唯一，停止操作")
    return matched[0] if matched else None


def select_node(nodes, selector):
    allowed = {"text", "content-desc", "resource-id"}
    if not selector or not set(selector).issubset(allowed):
        raise RuntimeError("缺少已经核实的页面控件标记")
    matches = [node for node in nodes if node.get("enabled") == "true" and
               all(node.get(key) == value for key, value in selector.items())]
    if len(matches) != 1:
        raise RuntimeError("目标控件不唯一或不可用，停止点击")
    bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", matches[0].get("bounds", ""))
    if not bounds:
        raise RuntimeError("控件位置无法确认")
    x1, y1, x2, y2 = map(int, bounds.groups())
    if x2 <= x1 or y2 <= y1:
        raise RuntimeError("控件没有可见面积")
    return matches[0], ((x1 + x2) // 2, (y1 + y2) // 2)


def sign_card(nodes, package):
    anchors = [i for i, node in enumerate(nodes) if node.get("text") == "赚珑珠" and node.get("resource-id") == package + ":id/title"]
    if len(anchors) != 1:
        return None
    cards = []
    for node in nodes[anchors[0] + 1:]:
        if node.get("text") == "花珑珠":
            break
        if node.get("resource-id") == package + ":id/img_item":
            cards.append(node)
    if len(cards) != 2:
        return None
    return cards[0]


def sign_button_score(image_path, bounds):
    from PIL import Image
    import cv2
    import numpy as np
    rectangle = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not rectangle:
        raise RuntimeError("签到卡片位置无法读取")
    with Image.open(image_path) as image:
        card = image.crop(tuple(map(int, rectangle.groups()))).resize((1022, 125))
        button = np.array(card.crop((800, 0, 1022, 125)).convert("L"))
    with Image.open(ROOT / "sign-button-template.png") as image:
        template = np.array(image.convert("L"))
    return float(cv2.matchTemplate(button, template, cv2.TM_CCOEFF_NORMED)[0, 0])


class Android:
    def __init__(self, config):
        self.serial = config.get("device_serial", "")
        self.package = config.get("package", "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", self.serial) or not re.fullmatch(r"com\.longfor\.[a-zA-Z0-9_.]+", self.package):
            raise RuntimeError("目标手机及龙湖APP包名尚未校准")

    def adb(self, *arguments, binary=False):
        result = subprocess.run([str(ADB), "-s", self.serial, *arguments], capture_output=True,
                                timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("手机连接或操作失败，已停止")
        return result.stdout if binary else result.stdout.decode("utf-8", errors="replace").strip()

    def shell(self, *arguments):
        return self.adb("shell", *arguments)

    def foreground(self):
        output = self.shell("dumpsys", "window")
        matches = re.findall(r"mCurrentFocus=Window\{[^\n}]*\su\d+\s+([^\s/]+)/", output)
        return matches[-1] if matches else ""

    def require_foreground(self):
        deadline = time.monotonic() + 6
        while True:
            self.require_unlocked()
            current = self.foreground()
            if current == self.package:
                return
            # Activity transitions can briefly have no focused window. Never
            # take input away from another foreground application.
            if current or time.monotonic() >= deadline:
                raise RuntimeError("前台不是已校准龙湖APP，或手机锁屏，停止操作")
            time.sleep(0.3)

    def require_unlocked(self):
        policy = self.shell("dumpsys", "window", "policy")
        showing = re.findall(r"^\s*showing=(true|false)\s*$", policy, re.MULTILINE)
        restricted = re.findall(r"^\s*inputRestricted=(true|false)\s*$", policy, re.MULTILINE)
        if showing != ["false"] or restricted != ["false"]:
            raise RuntimeError("手机已锁屏或锁屏状态无法确认，请先在手机上解锁")

    def wake(self, time_guard=lambda: None):
        time_guard()
        self.shell("input", "keyevent", "224")
        policy = self.shell("dumpsys", "window", "policy")
        secure = re.findall(r"^\s*secure=(true|false)\s*$", policy, re.MULTILINE)
        showing = re.findall(r"^\s*showing=(true|false)\s*$", policy, re.MULTILINE)
        if secure != ["false"]:
            raise RuntimeError("手机重新启用了锁屏密码，需要人工解锁，停止自动操作")
        if showing == ["true"]:
            time_guard()
            self.shell("wm", "dismiss-keyguard")
            time.sleep(0.5)
        self.require_unlocked()

    def launch(self, time_guard=lambda: None):
        if self.adb("get-state") != "device":
            raise RuntimeError("指定手机没有在线授权连接")
        if not self.shell("pm", "list", "packages", self.package).splitlines() == ["package:" + self.package]:
            raise RuntimeError("龙湖APP未安装，或包名不一致")
        self.wake(time_guard)
        component = self.shell("cmd", "package", "resolve-activity", "--brief", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", self.package).splitlines()[-1]
        if not re.fullmatch(re.escape(self.package) + r"/[A-Za-z0-9_.$]+", component):
            raise RuntimeError("龙湖APP启动入口无法确认")
        time_guard()
        self.shell("am", "start", "-W", "-n", component)
        for _ in range(10):
            if self.foreground() == self.package:
                return
            time.sleep(1)
        raise RuntimeError("手机锁屏或龙湖APP未进入前台，请人工处理")

    def snapshot(self):
        self.require_foreground()
        try:
            self.shell("uiautomator", "dump", "--compressed", REMOTE_VIEW)
            self.require_foreground()
            xml = self.adb("exec-out", "cat", REMOTE_VIEW)
            nodes = parse_nodes(xml, self.package)
            if not nodes:
                raise RuntimeError("无法读取目标APP页面控件")
            return nodes
        finally:
            self.shell("rm", "-f", REMOTE_VIEW)

    def screenshot(self, path):
        self.require_foreground()
        pixels = self.adb("exec-out", "screencap", "-p", binary=True)
        self.require_foreground()
        if not pixels.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("手机未返回有效截图")
        Path(path).write_bytes(pixels)

    def tap(self, selector, required_labels=(), required_ids=(), time_guard=lambda: None):
        nodes = self.snapshot()
        ensure_safe(nodes)
        ids = [node.get("resource-id", "") for node in nodes]
        if not (required_labels or required_ids) or not all(marker in labels(nodes) for marker in required_labels) or not all(marker in ids for marker in required_ids):
            raise RuntimeError("当前页面缺少已校准标记，停止点击")
        _, (x, y) = select_node(nodes, selector)
        time_guard()
        self.require_foreground()
        self.shell("input", "tap", str(x), str(y))

    def login_tap(self, profile, time_guard=lambda: None):
        nodes = self.snapshot()
        if not login_required(nodes) or not choose_login_profile(nodes, [profile]):
            raise RuntimeError("当前页面不符合已核实登录入口")
        _, (x, y) = select_node(nodes, profile["selector"])
        time_guard()
        self.require_foreground()
        self.shell("input", "tap", str(x), str(y))

    def prepare_sign_card(self, account_hash, time_guard=lambda: None):
        capture = ROOT / "entry-check.png"
        try:
            for attempt in range(9):
                time_guard()
                nodes = self.snapshot()
                ensure_safe(nodes)
                nicknames = [node.get("text", "") for node in nodes if node.get("resource-id") == self.package + ":id/tv_nickname"]
                if len(nicknames) != 1 or hashlib.sha256(nicknames[0].encode("utf-8")).hexdigest() != account_hash:
                    raise RuntimeError("会员账号无法核实，停止签到")
                if "会员" not in labels(nodes) or "我的珑珠" not in labels(nodes):
                    raise RuntimeError("当前页面不是已校准的会员页面")
                card = sign_card(nodes, self.package)
                tabs = [node for node in nodes if node.get("resource-id") == self.package + ":id/tab_text" and node.get("text") == "会员"]
                if len(tabs) != 1:
                    raise RuntimeError("会员页底部标记变化")
                tab_rect = list(map(int, re.findall(r"\d+", tabs[0]["bounds"])))
                if card:
                    x1, y1, x2, y2 = map(int, re.findall(r"\d+", card["bounds"]))
                    if y1 > tab_rect[3] * 0.15 and y2 < tab_rect[1] - 40 and x2 > x1 and y2 > y1:
                        self.screenshot(capture)
                        score = sign_button_score(capture, card["bounds"])
                        if score < 0.96:
                            raise RuntimeError("APP签到按钮与核实图像不一致，停止点击")
                        return card
                if attempt == 8:
                    break
                time_guard()
                self.require_foreground()
                width, height = tab_rect[2], tab_rect[3]
                self.shell("input", "swipe", str(width // 2), str(int(height * 0.75)), str(width // 2), str(int(height * 0.45)), "400")
                time.sleep(1)
            raise RuntimeError("限定滚动次数内未找到签到入口")
        finally:
            capture.unlink(missing_ok=True)

    def tap_sign_card(self, account_hash, time_guard=lambda: None, before_submit=lambda: None):
        card = self.prepare_sign_card(account_hash, time_guard)
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", card["bounds"]))
        time_guard()
        self.require_foreground()
        before_submit()
        time_guard()
        self.shell("input", "tap", str(int(x1 + (x2 - x1) * 0.9)), str((y1 + y2) // 2))

    def home(self):
        self.require_foreground()
        self.shell("input", "keyevent", "3")

    def finish_and_sleep(self):
        self.home()
        self.shell("input", "keyevent", "223")
        for _ in range(5):
            if "screenState=SCREEN_STATE_OFF" in self.shell("dumpsys", "window", "policy"):
                return
            time.sleep(0.5)
        raise RuntimeError("已返回桌面，但未确认手机熄屏")


if __name__ == "__main__":
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    phone = Android(config)
    print(json.dumps({"foreground_matches": phone.foreground() == phone.package}, ensure_ascii=False))
