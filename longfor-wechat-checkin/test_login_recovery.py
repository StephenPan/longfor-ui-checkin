"""Session-expiry and focus regressions using fictitious account data."""
import hashlib
from datetime import datetime
import unittest
from unittest.mock import patch

import checkin


def page(*labels):
    return {"elements": [{"name": label} for label in labels]}


class LoginRecovery(unittest.TestCase):
    def setUp(self):
        self.phone = "100****0000"
        self.config = {"account_hash": hashlib.sha256("测试会员".encode()).hexdigest(),
                       "login": {"enabled": True, "quick_login_consent": True,
                                 "phone_hash": hashlib.sha256(self.phone.encode()).hexdigest()}}
        self.entry = page("立即登录", "登录查看成长值及会员等级", "会员", "我的珑珠")
        self.quick = page("欢迎登录龙湖", "快捷登录", "我已阅读并同意")
        self.consent = page("服务协议及隐私保护", "同意", "欢迎登录龙湖")
        self.authorize = page("申请注册并验证你的手机号", "上次登录", self.phone)
        self.member = page("测试会员", "我的珑珠", "会员")

    def test_unchanged_and_partial_views_wait_for_ready_next_stage(self):
        snapshots = [{}, self.entry, page("欢迎登录龙湖", "快捷登录"), self.quick,
                     {}, self.quick, page("服务协议及隐私保护"), self.consent,
                     {}, self.consent, page("申请注册并验证你的手机号"), self.authorize,
                     {}, self.authorize, self.member]
        with patch.object(checkin, "bridge", side_effect=snapshots) as bridge, patch.object(checkin.time, "sleep"), patch.object(checkin, "log") as log:
            self.assertEqual(checkin.ensure_logged_in(self.entry, self.config, False), self.member)
        actions = [call for call in bridge.call_args_list if call.args == ("login-click",)]
        self.assertEqual([call.kwargs["name"] for call in actions], ["立即登录", "快捷登录", "同意", self.phone])
        self.assertNotIn(self.phone, str(log.call_args_list))

    def test_partial_quick_login_controls_cannot_be_clicked(self):
        snapshot = page("欢迎登录龙湖", "快捷登录")
        self.assertEqual(checkin.login_stage(snapshot), "loading")
        with self.assertRaises(RuntimeError):
            checkin.login_step(snapshot, self.config)

    def test_phone_authorization_can_temporarily_reveal_prior_login_layers(self):
        snapshots = [{}, self.quick, {}, self.consent, {}, self.authorize,
                     {}, self.quick, self.consent, self.authorize, self.member]
        with patch.object(checkin, "bridge", side_effect=snapshots) as bridge, patch.object(checkin.time, "sleep"), patch.object(checkin, "log"):
            self.assertEqual(checkin.ensure_logged_in(self.entry, self.config, False), self.member)
        actions = [call.kwargs["name"] for call in bridge.call_args_list if call.args == ("login-click",)]
        self.assertEqual(actions, ["立即登录", "快捷登录", "同意", self.phone])

    def test_old_layer_persisting_after_request_never_resends_login(self):
        with patch.object(checkin, "bridge", side_effect=[{}, self.quick, {}, self.quick]) as bridge, patch.object(checkin.time, "monotonic", side_effect=[0, 0, 1, 1, 40]), patch.object(checkin.time, "sleep"), patch.object(checkin, "log"):
            with self.assertRaises(RuntimeError):
                checkin.ensure_logged_in(self.entry, self.config, False)
        actions = [call.kwargs["name"] for call in bridge.call_args_list if call.args == ("login-click",)]
        self.assertEqual(actions, ["立即登录", "快捷登录"])

    def test_standalone_consent_and_expired_text_block_sign_actions(self):
        for snapshot in (self.consent, page("登录已过期，请重新登录"), page("请先登录")):
            self.assertTrue(checkin.login_required(snapshot))
            with self.assertRaises(RuntimeError):
                checkin.ensure_safe(snapshot)

    def test_phone_mismatch_ambiguity_and_missing_previous_login_never_click(self):
        snapshots = [page("申请注册并验证你的手机号", "上次登录", "100****0001"),
                     page("申请注册并验证你的手机号", "上次登录", self.phone, "100****0001"),
                     page("申请注册并验证你的手机号", self.phone)]
        with patch.object(checkin, "bridge") as bridge, patch.object(checkin, "log"):
            for snapshot in snapshots:
                with self.assertRaises(RuntimeError):
                    checkin.ensure_logged_in(snapshot, self.config, False)
        bridge.assert_not_called()

    def test_unauthorized_consent_or_disabled_recovery_never_click(self):
        for snapshot, config in [(self.consent, {**self.config, "login": {**self.config["login"], "quick_login_consent": False}}),
                                 (self.entry, {**self.config, "login": {"enabled": False}})]:
            with patch.object(checkin, "bridge") as bridge, patch.object(checkin, "log"):
                with self.assertRaises(RuntimeError):
                    checkin.ensure_logged_in(snapshot, config, False)
            bridge.assert_not_called()

    def test_wrong_account_after_authorization_never_continues(self):
        with patch.object(checkin, "bridge", side_effect=[{}, page("其他会员", "我的珑珠", "会员")]) as bridge, patch.object(checkin, "log"), patch.object(checkin.time, "sleep"):
            with self.assertRaises(RuntimeError):
                checkin.ensure_logged_in(self.authorize, self.config, False)
        self.assertEqual([call.args for call in bridge.call_args_list], [("login-click",), ()])

    def test_no_action_retry_when_login_click_fails(self):
        with patch.object(checkin, "bridge", side_effect=RuntimeError("Target window is not foreground; refusing input")) as bridge, patch.object(checkin, "log"):
            with self.assertRaises(RuntimeError):
                checkin.ensure_logged_in(self.entry, self.config, False)
        bridge.assert_called_once_with("login-click", name="立即登录")

    def test_focus_retry_is_inspection_only_and_recovers_logged_out_page(self):
        focus = RuntimeError("Target window is not foreground; refusing input")
        with patch.object(checkin, "bridge", side_effect=[focus, self.entry, self.entry]) as bridge, patch.object(checkin.time, "sleep"), patch.object(checkin, "log") as log:
            result = checkin.wait_for(checkin.login_required, startup=True, allow_login=True)
        self.assertEqual(result, self.entry)
        self.assertEqual([call.kwargs for call in bridge.call_args_list], [{"foreground": "true"}, {}, {"foreground": "true"}])
        self.assertTrue(all(not call.args for call in bridge.call_args_list))
        self.assertEqual(log.call_args.kwargs["snapshot"]["login_stage"], "entry")

    def test_persistent_focus_failure_is_bounded_without_input(self):
        focus = RuntimeError("Target window is not foreground; refusing input")
        with patch.object(checkin, "bridge", side_effect=[focus, self.entry] * 3) as bridge, patch.object(checkin.time, "sleep"), patch.object(checkin, "log") as log:
            with self.assertRaisesRegex(RuntimeError, "连续3次"):
                checkin.wait_for(checkin.login_required, startup=True)
        self.assertEqual(bridge.call_count, 6)
        self.assertEqual(log.call_count, 3)
        self.assertTrue(all(not call.args for call in bridge.call_args_list))

    def test_locked_desktop_and_cutoff_stop_focus_recovery(self):
        with patch.object(checkin, "bridge", side_effect=RuntimeError("Desktop locked or unavailable; user must unlock it")) as bridge:
            with self.assertRaises(RuntimeError):
                checkin.wait_for(lambda value: True, startup=True)
            bridge.assert_called_once()
        early = datetime(2026, 10, 22, 7, 29, 59, tzinfo=checkin.BEIJING)
        late = datetime(2026, 10, 22, 7, 30, tzinfo=checkin.BEIJING)
        with patch.object(checkin, "now", side_effect=[early, late]), patch.object(checkin, "bridge", side_effect=[RuntimeError("Target window is not foreground; refusing input"), self.entry]) as bridge, patch.object(checkin, "log"), patch.object(checkin.time, "sleep"):
            with self.assertRaises(RuntimeError):
                checkin.wait_for(lambda value: True, startup=True, scheduled=True)
        self.assertEqual(bridge.call_count, 2)


if __name__ == "__main__":
    unittest.main()
