"""Middle-layer contract tests. No provider is called and no picture is rendered."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from director.errors import DirectorRefusal
from director.middle import (
    approve,
    character_sheet,
    compile_prompt,
    mannequin_passes,
    pick_trend,
    prepare,
    recovery,
    video_mode,
)
from director.server import DirectorHandler
from director.service import DirectorApp
from tests.test_director_contract import MockTransport, ROOT


class MiddleTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-middle"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport())
        self.handler = DirectorHandler(self.app, ROOT)
        self.when = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.data, ignore_errors=True)

    def test_sheet_limits_and_alias(self):
        sheet = character_sheet("driver", ["photo-a", "photo-b"])
        self.assertEqual(sheet["alias"], "@driver")
        self.assertEqual(len(sheet["views"]), 8)
        self.assertFalse(sheet["rendered"])
        self.assertFalse(any(view["rendered"] for view in sheet["views"]))
        with self.assertRaises(DirectorRefusal) as caught:
            character_sheet("driver", ["x"] * 17)
        self.assertEqual(caught.exception.code, "sheet_photos_rejected")

    def test_video_modes_stay_distinct(self):
        edit = video_mode("edit")
        motion = video_mode("reference")
        self.assertEqual(edit["field"], "sourceVideo")
        self.assertTrue(edit["keeps_frames"])
        self.assertEqual(motion["field"], "referenceVideo")
        self.assertTrue(motion["motion_only"])

    def test_gta_plan_gates_and_expiry(self):
        raw = json.dumps({
            "wish": "face of user into GTA-style 13 s clip as the driver",
            "photo_asset_id": "photo-synthetic",
            "video_asset_id": "video-synthetic",
            "name": "driver",
            "duration_seconds": 13,
            "video_mode": "edit",
        }).encode()
        status, payload, _media = self.handler.handle("POST", "/api/director/middle/plan", raw, {})
        self.assertEqual(status, 200, payload)
        plan = payload["plan"]
        self.assertFalse(plan["render_started"])
        self.assertFalse(plan["generation_performed"])
        self.assertEqual(plan["video_mode"]["mode"], "edit")
        self.assertEqual([frame["role"] for frame in plan["frames"]["frames"]], ["first", "key", "last"])
        self.assertEqual([item["index"] for item in plan["mannequin"]["passes"]], [1, 2])
        self.assertIn("PRESERVE", plan["prompt"]["text"])
        self.assertIn("REPLACE", plan["prompt"]["text"])
        self.assertIn("INTEGRATE", plan["prompt"]["text"])
        self.assertEqual(plan["prompt"]["timeline"][0]["end"], "13.00")
        self.assertEqual(plan["draft"]["draft"]["resolution"], "480p")
        self.assertEqual(plan["draft"]["chunks"], [{"index": 1, "start_seconds": 0.0, "duration_seconds": 13.0}])
        self.assertEqual(plan["routing"][0]["model"], "seedance-2.5")
        self.assertFalse(plan["launch"]["approved"])
        self.assertTrue(any("gta" in (item.get("slug") or "") for item in plan["trends"]))
        self.assertNotIn("prompt", plan["trends"][0])
        created = datetime.fromisoformat(plan["launch"]["created_at"])
        with self.assertRaises(DirectorRefusal):
            approve(plan, "launch", created)
        approve(plan, "sheet", created)
        approve(plan, "frames", created)
        approve(plan, "launch", created + timedelta(minutes=1))
        self.assertTrue(plan["launch"]["approved"])
        self.assertFalse(plan["render_started"])
        plan["launch"]["approved"] = False
        with self.assertRaises(DirectorRefusal) as caught:
            approve(plan, "launch", created + timedelta(minutes=16))
        self.assertEqual(caught.exception.code, "card_expired")
        self.assertEqual(self.app.venice_transport.calls, [])

    def test_long_clip_is_split_and_reference_is_not_edit(self):
        plan = prepare({
            "wish": "repeat the motion only",
            "photo_asset_id": "p",
            "video_asset_id": "v",
            "name": "driver",
            "duration_seconds": 48,
            "video_mode": "reference",
        })
        self.assertEqual(plan["video_mode"]["field"], "referenceVideo")
        self.assertEqual([item["duration_seconds"] for item in plan["draft"]["chunks"]], [15.0, 15.0, 18.0])
        compiled = compile_prompt("kling", "@driver", "reference", 10, "a short motion")
        self.assertFalse(compiled["aliases"])
        with self.assertRaises(DirectorRefusal) as caught:
            compile_prompt("kling", "@driver", "reference", 13, "too long")
        self.assertEqual(caught.exception.code, "duration_rejected")

    def test_recovery_does_not_rewrite_and_trends_have_no_prompt_text(self):
        lever = recovery("not_like")
        self.assertFalse(lever["rewrote_prompt"])
        self.assertIn("shorter_chunk", lever["levers"])
        found = pick_trend("gta", 3)
        self.assertTrue(found)
        self.assertNotIn("prompt", found[0])
        passes = mannequin_passes("@driver")
        self.assertEqual(passes["passes"][0]["suit"], "green")
        self.assertIn("original actor", passes["passes"][1]["note"])


if __name__ == "__main__":
    unittest.main()
