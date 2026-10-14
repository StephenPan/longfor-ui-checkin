import json
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
from datetime import datetime, timezone
import checkin


def page(*labels):
    return {"elements": [{"name": label} for label in labels]}


class CriticalSafeguards(unittest.TestCase):
    def login_config(self):
        import hashlib
        return {"account_hash": hashlib.sha256("账号A".encode()).hexdigest(), "login": {"enabled": True, "quick_login_consent": True, "phone_hash": hashlib.sha256("100****0000".encode()).hexdigest()}}

    def test_expired_session_follows_verified_login_flow(self):
        config = self.login_config()
        initial = page("立即登录", "登录查看成长值及会员等级", "会员")
        quick = page("欢迎登录龙湖", "快捷登录", "我已阅读并同意")
        agreement = page("欢迎登录龙湖", "服务协议及隐私保护", "同意")
        phone = page("龙湖｜善待你一生", "申请注册并验证你的手机号", "上次登录", "100****0000")
        logged = page("账号A", "我的珑珠", "会员")
        calls = [{}, quick, {}, agreement, {}, phone, {}, logged]
        with patch.object(checkin, "bridge", side_effect=calls) as action, patch.object(checkin, "log"), patch.object(checkin.time, "sleep"):
            self.assertEqual(checkin.ensure_logged_in(initial, config, False), logged)
            inputs = [call for call in action.call_args_list if call.args == ("login-click",)]
            self.assertEqual([call.kwargs["name"] for call in inputs], ["立即登录", "快捷登录", "同意", "100****0000"])
            self.assertTrue(all(call.args != ("click-sign-entry",) for call in action.call_args_list))

    def test_login_requires_authorized_consent_and_matching_phone(self):
        config = self.login_config()
        config["login"]["quick_login_consent"] = False
        with self.assertRaises(RuntimeError):
            checkin.login_step(page("服务协议及隐私保护"), config)
        with self.assertRaises(RuntimeError):
            checkin.login_step(page("申请注册并验证你的手机号", "上次登录", "100****9999"), config)

    def test_login_never_skips_challenge_or_wrong_account(self):
        config = self.login_config()
        with patch.object(checkin, "bridge") as action:
            for snapshot in (page("账号A", "验证码"), page("账号B", "我的珑珠")):
                with self.assertRaises(RuntimeError):
                    checkin.ensure_logged_in(snapshot, config, False)
            action.assert_not_called()

    def test_login_cutoff_does_not_click(self):
        with patch.object(checkin, "now", return_value=datetime(2026, 10, 12, 7, 30, tzinfo=checkin.BEIJING)), patch.object(checkin, "bridge") as action:
            with self.assertRaises(RuntimeError):
                checkin.ensure_logged_in(page("立即登录", "登录查看成长值及会员等级"), self.login_config(), True)
            action.assert_not_called()

    def test_beijing_time_boundaries(self):
        for hour, minute, expected in [(5, 59, False), (6, 0, True), (7, 29, True), (7, 30, False)]:
            self.assertEqual(checkin.in_window(datetime(2026, 10, 9, hour, minute, tzinfo=checkin.BEIJING)), expected)
        self.assertTrue(checkin.in_window(datetime(2026, 10, 8, 22, 10, tzinfo=timezone.utc)))

    def test_navigation_uses_longer_bounded_startup_wait_without_signing(self):
        import hashlib
        account = "测试账号"
        snapshot = page(account, "会员")
        config = {"enabled": True, "shortcut": "龙湖天街.lnk", "account_hash": hashlib.sha256(account.encode()).hexdigest()}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / config["shortcut"]).touch()
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            with patch.object(checkin, "ROOT", root), patch.object(checkin.os, "startfile", create=True), patch.object(checkin.time, "sleep"), patch.object(checkin, "wait_for", return_value=snapshot) as wait, patch.object(checkin, "perform") as action, patch.object(checkin, "log"):
                self.assertEqual(checkin.execute(scheduled=False, navigate_only=True), 0)
            self.assertEqual(wait.call_args.kwargs["timeout"], 120)
            action.assert_not_called()

    def test_calendar_rewards_do_not_prove_credit(self):
        self.assertIsNone(checkin.growth_evidence(page("每日签到", "+ 5明天", "5成长值")))
        evidence = checkin.growth_evidence(page("每日签到", "今日已获得", "5成长值"))
        self.assertEqual(evidence["kind"], "成长值")

    def test_unconfigured_primary_reward_is_never_success(self):
        self.assertIsNone(checkin.primary_evidence(page("每日签到", "今日已获得", "5成长值"), {"reward_kind": "珑珠", "success_markers": []}))

    def test_verification_and_login_stop_clicks(self):
        for label in ("请完成滑块验证码", "立即登录", "操作异常，请稍后重试"):
            with self.assertRaises(RuntimeError):
                checkin.ensure_safe(page(label))

    def test_crash_after_submit_cannot_submit_again(self):
        for status in ("submission_intent", "uncertain", "verified"):
            self.assertTrue(checkin.blocks_reentry({"date": "2026-10-09", "status": status}, "2026-10-09"))
        self.assertFalse(checkin.blocks_reentry({"date": "2026-10-08", "status": "verified"}, "2026-10-09"))

    def test_wrong_account_rejected(self):
        import hashlib
        expected = hashlib.sha256("账号A".encode()).hexdigest()
        self.assertTrue(checkin.account_matches(page("账号A"), expected))
        self.assertFalse(checkin.account_matches(page("账号B"), expected))

    def test_background_member_text_is_not_current_page(self):
        snapshot = page("会员", "我的珑珠")
        snapshot["elements"].extend([{"name": "Page-Frame", "type": "ControlType.Document"}, {"name": "珑珠明细", "type": "ControlType.Document"}])
        self.assertEqual(checkin.active_page(snapshot), "珑珠明细")

    def test_daily_amount_is_read_from_current_result(self):
        snapshot = page("每日签到", "今日已获得", "10成长值")
        snapshot["elements"].append({"name": "每日签到", "type": "ControlType.Document"})
        config = {"success_markers": ["每日签到", "今日已获得"], "evidence_type": "growth"}
        self.assertEqual(checkin.primary_evidence(snapshot, config)["display"], "10成长值")
        snapshot["elements"][-1]["name"] = "珑珠明细"
        self.assertIsNone(checkin.primary_evidence(snapshot, config))

    def test_dynamic_ids_and_changed_member_layout(self):
        snapshot = page("赚珑珠")
        snapshot["elements"].extend([
            {"type": "ControlType.Image", "class_name": "image", "automation_id": "dynamic123"},
            {"type": "ControlType.Image", "class_name": "image", "automation_id": "dynamic456"},
            {"name": "花珑珠"}])
        self.assertEqual(checkin.sign_card(snapshot)["automation_id"], "dynamic123")
        snapshot["elements"].pop(2)
        with self.assertRaises(RuntimeError):
            checkin.sign_card(snapshot)

    def test_nearby_participate_button_cannot_pass_sign_check(self):
        from PIL import Image
        import cv2
        import numpy as np
        with Image.open(checkin.ROOT / "sign-button-template.png") as image:
            target = np.array(image.convert("L"))
        with Image.open(checkin.ROOT / "test-neighbor-button.png") as image:
            neighbor = np.array(image.convert("L"))
        self.assertGreater(float(cv2.matchTemplate(target, target, cv2.TM_CCOEFF_NORMED)[0, 0]), 0.96)
        self.assertLess(float(cv2.matchTemplate(neighbor, target, cv2.TM_CCOEFF_NORMED)[0, 0]), 0.96)

    def test_each_verified_scale_rejects_neighbor_button(self):
        from PIL import Image
        import cv2
        import numpy as np
        targets = []
        for path in checkin.ROOT.glob("sign-button-template*.png"):
            with Image.open(path) as image:
                targets.append(np.array(image.convert("L")))
        for path in checkin.ROOT.glob("test-neighbor-button*.png"):
            with Image.open(path) as image:
                neighbor = np.array(image.convert("L"))
            for target in targets:
                self.assertLess(float(cv2.matchTemplate(neighbor, target, cv2.TM_CCOEFF_NORMED)[0, 0]), 0.96)

    def test_close_failure_preserves_verified_daily_state(self):
        state = {"date": checkin.now().date().isoformat(), "status": "verified", "reward": {"display": "5成长值"}}
        with TemporaryDirectory() as directory, patch.object(checkin, "ROOT", Path(directory)), patch.object(checkin, "log"), patch.object(checkin, "perform", side_effect=RuntimeError("窗口被遮挡")):
            checkin.finish_success({"return_route": [{"action": "close-applet"}]}, state, scheduled=True)
            saved = checkin.load_json(Path(directory) / "state.json", {})
            self.assertEqual(saved["status"], "verified")
            self.assertEqual(saved["cleanup"]["status"], "failed")
            self.assertTrue(checkin.blocks_reentry(saved, state["date"]))

    def test_unverified_signing_cannot_trigger_close(self):
        with patch.object(checkin, "perform") as action:
            with self.assertRaises(RuntimeError):
                checkin.finish_success({"return_route": [{"action": "close-applet"}]}, {"date": checkin.now().date().isoformat(), "status": "uncertain"}, scheduled=True)
            action.assert_not_called()

    def test_after_cutoff_only_close_cleanup_can_click(self):
        late = datetime(2026, 10, 11, 7, 31, tzinfo=checkin.BEIJING)
        with patch.object(checkin, "now", return_value=late), patch.object(checkin.time, "sleep"), patch.object(checkin, "bridge", side_effect=[page("会员"), {"closed": True}]) as action:
            checkin.perform({"action": "close-applet"}, scheduled=True)
            self.assertEqual(action.call_count, 2)
            with self.assertRaises(RuntimeError):
                checkin.perform({"action": "click-sign-entry"}, scheduled=True)
            self.assertEqual(action.call_count, 2)


if __name__ == "__main__":
    unittest.main()
