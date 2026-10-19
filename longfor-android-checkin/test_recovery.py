"""Recovery paths must never mistake retained content for a fresh sign-in."""
import hashlib
import unittest
from unittest.mock import Mock, patch

import checkin
from android_bridge import Android


PACKAGE = "com.longfor.supera"


class Recovery(unittest.TestCase):
    def setUp(self):
        self.config = {"package": PACKAGE, "account_hash": hashlib.sha256("测试会员".encode()).hexdigest(),
                       "enabled": True, "wake_mode": "unprotected", "success_markers": ["今日已获得"], "sign_page_title": "每日签到"}
        self.member = [{"text": "会员"}, {"text": "我的珑珠"},
                       {"resource-id": PACKAGE + ":id/tv_nickname", "text": "测试会员"}]
        self.old_sign = [{"resource-id": PACKAGE + ":id/id_browser_header_title", "text": "每日签到"},
                         {"text": "今日已获得"}, {"text": "5成长值"}]

    def phone(self, snapshots):
        phone = Mock(package=PACKAGE)
        phone.snapshot.side_effect = snapshots
        return phone

    def test_retained_sign_page_returns_before_account_check_without_crediting_it(self):
        phone = self.phone([self.old_sign, self.member])
        with patch.object(checkin, "log"), patch.object(checkin, "write_json") as save:
            self.assertEqual(checkin.navigate_member(phone, self.config, lambda: None), self.member)
        phone.back_from_page.assert_called_once()
        phone.tap_sign_card.assert_not_called()
        phone.tap.assert_not_called()
        save.assert_not_called()

    def test_popup_without_member_tab_is_dismissed_before_waiting_for_member(self):
        popup = [{"resource-id": PACKAGE + ":id/img_market"}, {"resource-id": PACKAGE + ":id/img_close"}]
        phone = self.phone([popup, self.member])
        with patch.object(checkin, "log"):
            self.assertEqual(checkin.navigate_member(phone, self.config, lambda: None), self.member)
        self.assertEqual(phone.tap.call_args.args[0], {"resource-id": PACKAGE + ":id/img_close"})
        phone.tap_sign_card.assert_not_called()

    def test_unknown_close_control_is_not_a_known_ad(self):
        self.assertFalse(checkin.market_popup_open([{"resource-id": PACKAGE + ":id/img_close"}], PACKAGE))

    def test_popup_after_switching_to_member_uses_same_bounded_recovery(self):
        home = [{"text": "首页"}, {"text": "会员"}]
        popup = [{"resource-id": PACKAGE + ":id/img_market"}, {"resource-id": PACKAGE + ":id/img_close"}]
        phone = self.phone([home, popup, self.member])
        with patch.object(checkin, "log"):
            self.assertEqual(checkin.navigate_member(phone, self.config, lambda: None), self.member)
        self.assertEqual([call.args[0] for call in phone.tap.call_args_list], [
            {"text": "会员", "resource-id": PACKAGE + ":id/tab_text"},
            {"resource-id": PACKAGE + ":id/img_close"}])

    def test_wrong_member_account_never_triggers_sign_or_login(self):
        wrong = [dict(node) for node in self.member]
        wrong[-1]["text"] = "其他会员"
        phone = self.phone([wrong])
        with self.assertRaises(RuntimeError):
            checkin.navigate_member(phone, self.config, lambda: None)
        phone.tap.assert_not_called()
        phone.tap_sign_card.assert_not_called()

    def test_repeated_popup_is_bounded(self):
        popup = [{"resource-id": PACKAGE + ":id/img_market"}]
        phone = self.phone([popup, popup, popup])
        with patch.object(checkin, "log"), self.assertRaises(RuntimeError):
            checkin.navigate_member(phone, self.config, lambda: None)
        self.assertEqual(phone.tap.call_count, 2)

    def test_challenge_over_ad_stops_before_close(self):
        phone = self.phone([[{"resource-id": PACKAGE + ":id/img_market", "text": "安全验证"}]])
        with self.assertRaises(RuntimeError):
            checkin.navigate_member(phone, self.config, lambda: None)
        phone.tap.assert_not_called()

    def test_back_checks_native_title_and_deadline_before_input(self):
        phone = Android({"device_serial": "test-phone", "package": PACKAGE})
        phone.snapshot = Mock(return_value=self.old_sign)
        phone.require_foreground = Mock()
        phone.shell = Mock()
        phone.back_from_page("每日签到")
        phone.shell.assert_called_once_with("input", "keyevent", "4")
        phone.shell.reset_mock()
        with self.assertRaises(RuntimeError):
            phone.back_from_page("其他页面")
        phone.shell.assert_not_called()
        with self.assertRaises(RuntimeError):
            phone.back_from_page("每日签到", Mock(side_effect=RuntimeError("截止")))
        phone.shell.assert_not_called()

    def test_verify_entry_never_submits_or_writes_sign_state(self):
        phone = Mock()
        with patch.object(checkin, "load_json", side_effect=[self.config, {}]), patch.object(checkin, "Android", return_value=phone), patch.object(checkin, "navigate_member", return_value=self.member), patch.object(checkin, "log"), patch.object(checkin, "mark_submission") as mark, patch.object(checkin, "write_json") as save:
            self.assertEqual(checkin.execute(scheduled=False, verify_entry=True), 0)
        phone.prepare_sign_card.assert_called_once()
        phone.finish_and_sleep.assert_called_once()
        phone.tap_sign_card.assert_not_called()
        mark.assert_not_called()
        save.assert_not_called()

    def test_timeout_diagnostics_omit_account_text(self):
        phone = self.phone([[{"text": "PRIVATE_ACCOUNT_TEXT"}]])
        with patch.object(checkin.time, "monotonic", side_effect=[0, 0, 40]), patch.object(checkin.time, "sleep"), patch.object(checkin, "log") as log:
            with self.assertRaises(RuntimeError):
                checkin.wait_snapshot(phone, lambda value: False, phase="startup")
        self.assertNotIn("PRIVATE_ACCOUNT_TEXT", str(log.call_args))
        self.assertEqual(log.call_args.kwargs["phase"], "startup")


if __name__ == "__main__":
    unittest.main()
