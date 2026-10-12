from datetime import datetime
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from android_bridge import Android, ensure_safe, parse_nodes, select_node, sign_button_score, login_required
from run_state import BEIJING, blocks_reentry, in_window, load_json, write_json
import checkin


class Guards(unittest.TestCase):
    def test_login_detects_redacted_sms_fields(self):
        nodes = parse_nodes('<hierarchy><node package="com.longfor.supera" class="android.widget.EditText" resource-id="com.longfor.supera:id/et_verify_code" text="123456"/></hierarchy>', "com.longfor.supera")
        self.assertTrue(login_required(nodes))

    def test_unconfigured_login_stops_before_sign(self):
        phone = Mock()
        nodes = [{"resource-id": "com.longfor.supera:id/login_root_view", "text": "手机号快捷登录"}]
        with patch.object(checkin, "log"), self.assertRaises(RuntimeError):
            checkin.restore_login(phone, {}, nodes, lambda: None)
        phone.login_tap.assert_not_called()

    def test_configured_login_continues_only_after_page_changes(self):
        phone = Mock()
        profile = {"name": "verified-login", "required_ids": ["com.longfor.supera:id/login_root_view"], "selector": {"text": "授权登录"}}
        nodes = [{"resource-id": "com.longfor.supera:id/login_root_view"}, {"text": "授权登录", "enabled": "true", "bounds": "[1,1][100,100]"}]
        member = [{"text": "会员"}]
        with patch.object(checkin, "wait_snapshot", return_value=member), patch.object(checkin, "log"):
            result = checkin.restore_login(phone, {"login": {"profiles": [profile]}}, nodes, lambda: None)
        self.assertEqual(result, member)
        phone.login_tap.assert_called_once()

    def test_login_never_clicks_through_challenge(self):
        phone = Mock()
        with patch.object(checkin, "log"), self.assertRaises(RuntimeError):
            checkin.restore_login(phone, {}, [{"text": "请完成滑块安全验证"}], lambda: None)
        phone.login_tap.assert_not_called()

    def test_transient_empty_foreground_waits_without_stealing_focus(self):
        phone = Android({"device_serial": "test-phone", "package": "com.longfor.supera"})
        phone.require_unlocked = Mock()
        phone.foreground = Mock(side_effect=["", "com.longfor.supera"])
        phone.shell = Mock()
        with patch("android_bridge.time.sleep"):
            phone.require_foreground()
        phone.shell.assert_not_called()

    def test_other_foreground_application_is_not_treated_as_transition(self):
        phone = Android({"device_serial": "test-phone", "package": "com.longfor.supera"})
        phone.require_unlocked = Mock()
        phone.foreground = Mock(return_value="com.other.app")
        with self.assertRaises(RuntimeError):
            phone.require_foreground()

    def test_beijing_window_edges(self):
        for hour, minute, expected in [(5, 59, False), (6, 0, True), (7, 29, True), (7, 30, False)]:
            self.assertEqual(in_window(datetime(2026, 10, 11, hour, minute, tzinfo=BEIJING)), expected)

    def test_submission_or_uncertain_prevents_same_day_retry(self):
        for status in ("verified", "already_signed", "submission_intent", "uncertain"):
            self.assertTrue(blocks_reentry({"date": "2026-10-11", "status": status}, "2026-10-11"))
            self.assertFalse(blocks_reentry({"date": "2026-10-10", "status": status}, "2026-10-11"))

    def test_unknown_or_secure_lock_stops(self):
        phone = Android({"device_serial": "test-phone", "package": "com.longfor.supera"})
        for policy in ("showing=true\ninputRestricted=false", "", "showing=false\ninputRestricted=true"):
            phone.shell = lambda *args: policy
            with self.assertRaises(RuntimeError):
                phone.require_unlocked()
        phone.shell = lambda *args: "showing=false\ninputRestricted=false"
        phone.require_unlocked()

    def test_login_fields_redacted_but_login_guard_retained(self):
        nodes = parse_nodes('<hierarchy><node package="com.longfor.supera" resource-id="com.longfor.supera:id/et_verify_code" class="android.widget.EditText" text="123456"/></hierarchy>', "com.longfor.supera")
        self.assertNotIn("123456", str(nodes))
        with self.assertRaises(RuntimeError):
            ensure_safe(nodes)

    def test_ambiguous_or_empty_bounds_refuses_click(self):
        node = {"text": "会员", "enabled": "true", "bounds": "[1,2][3,4]"}
        with self.assertRaises(RuntimeError):
            select_node([node, node], {"text": "会员"})
        with self.assertRaises(RuntimeError):
            select_node([{**node, "bounds": "[1,2][1,2]"}], {"text": "会员"})

    def test_atomic_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            payload = {"status": "submission_intent", "channel": "APP"}
            write_json(path, payload)
            self.assertEqual(load_json(path, {}), payload)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_neighbor_reward_button_does_not_match_sign(self):
        root = Path(__file__).resolve().parent
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "card.png"
            for name, expected in [("sign-button-template.png", True), ("test-neighbor-button.png", False)]:
                card = Image.new("RGB", (1022, 125), "white")
                with Image.open(root / name) as button:
                    card.paste(button.convert("RGB"), (800, 0))
                card.save(capture)
                score = sign_button_score(capture, "[0,0][1022,125]")
                self.assertEqual(score >= 0.96, expected)

    def test_wake_never_dismisses_secure_lock(self):
        phone = Android({"device_serial": "test-phone", "package": "com.longfor.supera"})
        phone.shell = Mock(return_value="showing=true\nsecure=true\ninputRestricted=true")
        with self.assertRaises(RuntimeError):
            phone.wake()
        self.assertNotIn(("wm", "dismiss-keyguard"), [call.args for call in phone.shell.call_args_list])

    def test_nonsecure_swipe_lock_can_be_dismissed(self):
        phone = Android({"device_serial": "test-phone", "package": "com.longfor.supera"})
        phone.shell = Mock(side_effect=["", "showing=true\nsecure=false\ninputRestricted=true", "", "showing=false\ninputRestricted=false"])
        with patch("android_bridge.time.sleep"):
            phone.wake()
        self.assertIn(("wm", "dismiss-keyguard"), [call.args for call in phone.shell.call_args_list])

    def evidence_fixture(self):
        config = {"package": "com.longfor.supera", "success_markers": ["APP签到 奖励翻倍", "今日已获得"], "sign_page_title": "每日签到"}
        nodes = [{"text": "APP签到 奖励翻倍"}, {"text": "今日已获得"}, {"text": "5成长值"}, {"text": "+ 15"},
                 {"text": "每日签到", "resource-id": config["package"] + ":id/id_browser_header_title"}]
        return nodes, config

    def test_only_today_credit_qualifies(self):
        nodes, config = self.evidence_fixture()
        self.assertEqual(checkin.primary_evidence(nodes, config)["display"], "5成长值")
        nodes[2]["text"] = "+ 5"
        self.assertIsNone(checkin.primary_evidence(nodes, config))

    def test_wrong_channel_or_missing_native_header_is_not_proof(self):
        nodes, config = self.evidence_fixture()
        nodes[0]["text"] = "小程序签到"
        self.assertIsNone(checkin.primary_evidence(nodes, config))
        nodes, config = self.evidence_fixture()
        self.assertIsNone(checkin.primary_evidence(nodes[:-1], config))

    def test_cleanup_failure_preserves_verified_reward(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(checkin, "ROOT", Path(directory)), patch.object(checkin, "log"):
            phone = Mock()
            phone.snapshot.side_effect = RuntimeError("connection lost")
            state = {"date": checkin.now().date().isoformat(), "status": "verified", "reward": {"display": "5成长值"}}
            checkin.finish_success(phone, {}, state)
            saved = load_json(Path(directory) / "state.json", {})
            self.assertEqual(saved["status"], "verified")
            self.assertEqual(saved["cleanup"]["status"], "failed")

    def test_outside_window_never_connects_phone(self):
        with patch.object(checkin, "now", return_value=datetime(2026, 10, 11, 7, 30, tzinfo=BEIJING)), patch.object(checkin, "load_json", return_value={}), patch.object(checkin, "log"), patch.object(checkin, "Android") as phone:
            self.assertEqual(checkin.execute(), 0)
            phone.assert_not_called()

    def test_same_day_submission_never_launches_phone(self):
        config = {"enabled": True, "wake_mode": "unprotected", "success_markers": ["today"], "account_hash": "abc"}
        state = {"date": "2026-10-11", "status": "submission_intent"}
        with patch.object(checkin, "now", return_value=datetime(2026, 10, 11, 6, 30, tzinfo=BEIJING)), patch.object(checkin, "load_json", side_effect=[config, state]), patch.object(checkin, "log"), patch.object(checkin, "Android") as phone:
            self.assertEqual(checkin.execute(), 0)
            phone.assert_not_called()


if __name__ == "__main__":
    unittest.main()
