"""Read-only recovery and moving-frame regressions with synthetic snapshots."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import checkin


def entry(y=300):
    return {"window": {"x": 0, "y": 0, "width": 642, "height": 1181}, "elements": [
        {"name": "赚珑珠"},
        {"type": "ControlType.Image", "class_name": "image", "automation_id": "sign", "x": 50, "y": y, "width": 529, "height": 66, "offscreen": False},
        {"type": "ControlType.Image", "class_name": "image", "automation_id": "neighbor"},
        {"name": "花珑珠"}, {"class_name": "tabbar_item tabbar_item_0", "y": 1000}]}


class Recovery(unittest.TestCase):
    def test_transient_image_mismatch_waits_without_clicking(self):
        with TemporaryDirectory() as directory, patch.object(checkin, "ROOT", Path(directory)), patch.object(checkin, "bridge", return_value=entry()) as bridge, patch.object(checkin, "button_score", side_effect=[0.4, 0.99]) as score, patch.object(checkin.time, "sleep"), patch.object(checkin, "log"):
            self.assertEqual(checkin.prepare_sign_entry(False)["automation_id"], "sign")
        self.assertEqual(score.call_count, 2)
        self.assertTrue(all(not call.args for call in bridge.call_args_list))

    def test_persistent_mismatch_remains_fail_closed(self):
        with TemporaryDirectory() as directory, patch.object(checkin, "ROOT", Path(directory)), patch.object(checkin, "bridge", return_value=entry()) as bridge, patch.object(checkin, "button_score", return_value=0.4) as score, patch.object(checkin.time, "sleep"), patch.object(checkin, "log"):
            with self.assertRaises(RuntimeError):
                checkin.prepare_sign_entry(False)
        self.assertEqual(score.call_count, 3)
        self.assertTrue(all(not call.args for call in bridge.call_args_list))

    def test_moving_capture_is_not_scored_until_geometry_stabilizes(self):
        snapshots = [entry(300), entry(250), entry(250), entry(250), entry(250), entry(250)]
        with TemporaryDirectory() as directory, patch.object(checkin, "ROOT", Path(directory)), patch.object(checkin, "bridge", side_effect=snapshots), patch.object(checkin, "button_score", return_value=0.99) as score, patch.object(checkin.time, "sleep"), patch.object(checkin, "log") as log:
            self.assertEqual(checkin.prepare_sign_entry(False)["y"], 250)
        score.assert_called_once()
        self.assertTrue(any(call.args == ("entry_layout_moved",) for call in log.call_args_list))

    def test_nonfinite_match_score_cannot_qualify(self):
        with TemporaryDirectory() as directory, patch.object(checkin, "ROOT", Path(directory)), patch.object(checkin, "bridge", return_value=entry()), patch.object(checkin, "button_score", return_value=float("nan")), patch.object(checkin.time, "sleep"), patch.object(checkin, "log") as log:
            with self.assertRaises(RuntimeError):
                checkin.prepare_sign_entry(False)
        scores = [call.kwargs["score"] for call in log.call_args_list if call.args == ("entry_image_check",)]
        self.assertEqual(scores, [None, None, None])

    def test_partial_card_behind_footer_is_not_visible(self):
        snapshot = entry(980)
        self.assertFalse(checkin.card_visible(snapshot, checkin.sign_card(snapshot)))

    def test_wrong_capture_dimensions_stop_matching(self):
        from PIL import Image
        with TemporaryDirectory() as directory:
            image = Path(directory) / "small.png"
            Image.new("RGB", (20, 20)).save(image)
            with self.assertRaises(RuntimeError):
                checkin.button_score(image, checkin.sign_card(entry()), entry()["window"])

    def test_startup_waits_for_window_creation_and_restores_foreground(self):
        snapshot = {"elements": [{"name": "会员"}]}
        with patch.object(checkin, "bridge", side_effect=[RuntimeError("Expected one visible 龙湖天街 WeChatAppEx window; found 0"), snapshot]) as bridge, patch.object(checkin.time, "sleep"):
            self.assertEqual(checkin.wait_for(lambda value: "会员" in checkin.names(value), startup=True), snapshot)
        self.assertTrue(all(call.kwargs == {"foreground": "true"} for call in bridge.call_args_list))

    def test_multiple_windows_and_cutoff_never_retry_or_click(self):
        with patch.object(checkin, "bridge", side_effect=RuntimeError("Expected one visible 龙湖天街 WeChatAppEx window; found 2")) as bridge:
            with self.assertRaises(RuntimeError):
                checkin.wait_for(lambda value: True, startup=True)
            bridge.assert_called_once()
        with patch.object(checkin, "now", return_value=datetime(2026, 10, 19, 7, 30, tzinfo=checkin.BEIJING)), patch.object(checkin, "bridge") as bridge:
            with self.assertRaises(RuntimeError):
                checkin.wait_for(lambda value: True, startup=True, scheduled=True)
            bridge.assert_not_called()

    def navigation(self, root, snapshots, verify_entry=False):
        config = {"enabled": True, "shortcut": "fixture.lnk", "account_hash": hashlib.sha256("测试会员".encode()).hexdigest()}
        (root / "fixture.lnk").touch()
        (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        def observed(predicate, **options):
            snapshot = snapshots.pop(0)
            self.assertTrue(predicate(snapshot))
            return snapshot
        with patch.object(checkin, "ROOT", root), patch.object(checkin.os, "startfile", create=True), patch.object(checkin.time, "sleep"), patch.object(checkin, "wait_for", side_effect=observed), patch.object(checkin, "perform") as perform, patch.object(checkin, "prepare_sign_entry") as prepare, patch.object(checkin, "write_json") as save, patch.object(checkin, "log"):
            self.assertEqual(checkin.execute(scheduled=False, navigate_only=not verify_entry, verify_entry=verify_entry), 0)
        save.assert_not_called()
        return perform, prepare

    def test_retained_sign_page_is_accepted_for_back_navigation_only(self):
        retained = {"elements": [{"name": "每日签到", "type": "ControlType.Document"}, {"name": "今日已获得"}, {"name": "5成长值"}]}
        member = {"elements": [{"name": "会员"}, {"name": "测试会员"}]}
        with TemporaryDirectory() as directory:
            perform, prepare = self.navigation(Path(directory), [retained, member])
        self.assertEqual(perform.call_args.args[0]["action"], "click-class")
        self.assertEqual(perform.call_args.args[0]["class"], "back")
        prepare.assert_not_called()

    def test_verify_entry_never_runs_submission_route(self):
        member = {"elements": [{"name": "会员"}, {"name": "测试会员"}]}
        with TemporaryDirectory() as directory:
            perform, prepare = self.navigation(Path(directory), [member], verify_entry=True)
        perform.assert_not_called()
        prepare.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
