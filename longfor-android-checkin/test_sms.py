"""Authentication boundary tests; all phone/code fixtures are synthetic."""
from datetime import datetime
from unittest.mock import Mock, patch
import unittest

import login_sms as sms
from run_state import BEIJING


class SmsBoundaries(unittest.TestCase):
    def page(self):
        package = "com.longfor.supera"
        nodes = [{"resource-id": package + ":id/" + name} for name in ("login_root_view", "et_login_mobile", "et_verify_code", "tv_welcome")]
        nodes.append({"resource-id": package + ":id/login_agree_checked", "checked": "true"})
        return nodes

    def test_windows_encryption_round_trip_does_not_leave_plain_value(self):
        value = b"synthetic-local-fixture"
        cipher = sms.crypt_phone(value)
        self.assertNotIn(value, cipher)
        self.assertEqual(sms.crypt_phone(cipher, decrypt=True), value)

    def test_wrong_phone_rejected_before_encryption_or_saving(self):
        with patch.object(sms, "crypt_phone") as encrypt, self.assertRaises(RuntimeError):
            sms.save_phone("10000009999", {"login": {"phone_hash": "different"}})
        encrypt.assert_not_called()

    def test_only_digits_enter_login_input(self):
        phone = Mock()
        for kind, value in [("code", "12;exit"), ("pin", "1234"), ("phone", "123")]:
            with self.assertRaises(RuntimeError):
                sms.set_digits(phone, kind, value)
        phone.snapshot.assert_not_called()

    def test_login_input_rejected_on_member_page(self):
        phone = Mock(package="com.longfor.supera")
        phone.snapshot.return_value = [{"text": "会员"}]
        with self.assertRaises(RuntimeError):
            sms.set_digits(phone, "code", "123456")
        phone.shell.assert_not_called()

    def test_request_intent_is_saved_before_sending_and_codes_are_not_recorded(self):
        phone = Mock(package="com.longfor.supera")
        phone.snapshot.side_effect = [self.page(), self.page() + [{"resource-id": "com.longfor.supera:id/tv_get_code", "enabled": "false", "text": "58s后重新获取"}]]
        events = []
        phone.login_tap.side_effect = lambda *args: events.append("tap")
        saved = []
        def save(path, payload):
            saved.append(dict(payload))
            events.append(payload["status"])
        with patch.object(sms, "load_phone", return_value="10000000000"), patch.object(sms, "load_json", return_value={}), patch.object(sms, "set_digits"), patch.object(sms, "write_json", side_effect=save), patch.object(sms.time, "sleep"):
            sms.request_sms(phone, {"login": {"app_consent_authorized": True}})
        self.assertEqual(events, ["request_intent", "tap", "code_requested"])
        self.assertTrue(all(not ({"phone", "code"} & set(row)) for row in saved))

    def test_existing_request_never_sends_second_sms(self):
        phone = Mock(package="com.longfor.supera")
        phone.snapshot.return_value = self.page()
        existing = {"date": sms.now().date().isoformat(), "time": sms.now().isoformat(), "status": "logged_in"}
        with patch.object(sms, "load_phone", return_value="10000000000"), patch.object(sms, "load_json", return_value=existing), self.assertRaises(RuntimeError):
            sms.request_sms(phone, {"login": {"app_consent_authorized": True}})
        phone.login_tap.assert_not_called()

    def test_login_completion_resumes_sign_only_in_window_and_without_prior_intent(self):
        morning = datetime(2026, 10, 12, 6, 30, tzinfo=BEIJING)
        with patch.object(sms, "now", return_value=morning), patch.object(sms, "load_json", return_value={}), patch("checkin.execute", return_value=0) as execute:
            self.assertEqual(sms.resume_signing({"enabled": True}), "resumed")
            execute.assert_called_once_with(scheduled=True)
        for status in ("verified", "submission_intent", "uncertain"):
            with patch.object(sms, "now", return_value=morning), patch.object(sms, "load_json", return_value={"date": "2026-10-12", "status": status}), patch("checkin.execute") as execute:
                self.assertEqual(sms.resume_signing({"enabled": True}), "already_done_or_pending_review")
                execute.assert_not_called()
        with patch.object(sms, "now", return_value=datetime(2026, 10, 12, 7, 30, tzinfo=BEIJING)), patch.object(sms, "load_json", return_value={}), patch("checkin.execute") as execute:
            self.assertEqual(sms.resume_signing({"enabled": True}), "outside_window")
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
