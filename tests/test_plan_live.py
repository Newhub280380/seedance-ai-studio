"""Optional live call to the operator's loopback brain.

Skipped unless DIRECTOR_PLAN_LIVE=1. CI does not set that. This file does not
embed private photos and does not treat a planned task as a finished film.
"""

import os
import unittest

from director.pngutil import solid_png
from director.service import DirectorApp
from tests.test_director_contract import MockTransport, ROOT


class PlanLiveTest(unittest.TestCase):
    def test_live_brain_does_not_fabricate_a_film(self):
        if os.environ.get("DIRECTOR_PLAN_LIVE") != "1":
            self.skipTest("set DIRECTOR_PLAN_LIVE=1 on the box to call DIRECTOR_BRAIN_URL")
        url = os.environ.get("DIRECTOR_BRAIN_URL") or ""
        model = os.environ.get("DIRECTOR_BRAIN_MODEL") or ""
        if not url or not model:
            self.fail("DIRECTOR_PLAN_LIVE=1 requires DIRECTOR_BRAIN_URL and DIRECTOR_BRAIN_MODEL")
        data = ROOT / "director-data-plan-live"
        if data.exists():
            import shutil
            shutil.rmtree(data)
        app = DirectorApp(ROOT, data, MockTransport(), MockTransport())
        colors = ((3, 3, 3), (6, 6, 6), (9, 9, 9), (12, 12, 12))
        roles = (("cast", "father"), ("cast", "daughter"), ("place", "rink"), ("style_motion", "style_reference"))
        refs = []
        for color, (role, label) in zip(colors, roles):
            asset_id = app.assets.put(solid_png(8, 8, color), "image/png")["id"]
            refs.append({"asset_id": asset_id, "role": role, "label": label})
        task = app.create_task({
            "capability": "director.plan",
            "client_request_id": "plan-live-1",
            "confirm": True,
            "brief": (
                "Private family material stays on this machine. Plan a 120 second vertical film about the daughter's "
                "own skating practice: hair, outfits, training, performance. The style reference is style and motion "
                "only, not a person she meets and not an endorsement. Keep ages and faces. Do not paste a face onto another body."
            ),
            "duration_seconds": 120,
            "aspect_ratio": "9:16",
            "references": refs,
        })
        self.assertIn(task["status"], ("planned", "failed"))
        self.assertFalse(task["render_started"])
        self.assertIsNone(task["film"])
        self.assertIsNone(task.get("result"))
        self.assertEqual(app.venice_transport.calls, [])
        if task["status"] == "planned":
            self.assertEqual(sum(shot["duration_seconds"] for shot in task["plan"]["shots"]), 120)
            self.assertFalse(task["plan"]["render_started"])
        else:
            self.assertIsNone(task["plan"])
            self.assertIn(task["error"]["code"], ("plan_invalid", "brain_rejected", "brain_unavailable", "brain_endpoint_rejected"))
        import shutil
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
