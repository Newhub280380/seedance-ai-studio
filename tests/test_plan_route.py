"""MOCKED local brain. Not a film, not a GPU render, and not a live model call.

A canned JSON reply is accepted only when it validates. Anything else stays
failed with plan is null. No static story is written in its place.
"""

import json
import unittest

from director.errors import DirectorRefusal
from director.plan_route import CONSTRAINTS
from director.pngutil import solid_png
from director.service import DirectorApp
from tests.test_director_contract import MockTransport, ROOT


def _reply(plan):
    return 200, {"choices": [{"message": {"content": json.dumps(plan)}}]}


class PlanRouteTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-plan-test"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport())
        self.calls = []
        self.app.brain_transport = self._transport
        self.app.brain_url = "http://127.0.0.1:18765/v1"
        self.app.brain_model = "local-test"
        self.app.studio_transport = self._studio
        self.plan = None
        self.refs = []
        colors = ((1, 2, 3), (4, 5, 6), (7, 8, 9), (2, 2, 2))
        roles = (
            ("cast", "father"),
            ("cast", "daughter"),
            ("place", "rink"),
            ("style_motion", "style_reference"),
        )
        for color, (role, label) in zip(colors, roles):
            asset_id = self.app.assets.put(solid_png(8, 8, color), "image/png")["id"]
            self.refs.append({"asset_id": asset_id, "role": role, "label": label})
        self.plan = self._valid()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.data, ignore_errors=True)

    def _transport(self, method, url, body, timeout):
        self.calls.append((method, url, body))
        if self.plan is None:
            return 200, {"choices": [{"message": {"content": "once upon a time"}}]}
        return _reply(self.plan)

    def _studio(self, method, url, body, timeout):
        raise AssertionError("planning called Studio %s %s" % (method, url))

    def _body(self, **extra):
        body = {
            "capability": "director.plan",
            "client_request_id": extra.pop("client_request_id", "plan-1"),
            "confirm": True,
            "brief": "A private story about the daughter's own skating practice, hair, outfits, training and performance. The style reference stays style and motion only.",
            "duration_seconds": 120,
            "aspect_ratio": "9:16",
            "references": self.refs,
        }
        body.update(extra)
        return body

    def _valid(self):
        ids = [item["asset_id"] for item in self.refs]
        shots = []
        cursor = 0
        for index, duration in enumerate((30, 30, 30, 30), start=1):
            shots.append({
                "index": index,
                "start_seconds": cursor,
                "duration_seconds": duration,
                "summary": "The daughter keeps her own age and face in this part.",
                "image_prompt": "Vertical frame of the daughter in her own clothes. Age unchanged.",
                "video_prompt": "The daughter trains. No meeting and no other person's body.",
                "picture_asset_ids": ids[:3] if index == 1 else [ids[1]],
                "style_asset_ids": [ids[3]] if index == 1 else [],
            })
            cursor += duration
        return {
            "title": "Private skating story",
            "logline": "She trains toward a performance that is her own.",
            "duration_seconds": 120,
            "aspect_ratio": "9:16",
            "constraints": dict(CONSTRAINTS),
            "references": [dict(item) for item in self.refs],
            "shots": shots,
            "render_started": False,
        }

    def test_valid_reply_is_a_plan_and_not_a_film(self):
        task = self.app.create_task(self._body())
        self.assertEqual(task["status"], "planned", task.get("error"))
        self.assertFalse(task["render_started"])
        self.assertIsNone(task["film"])
        self.assertIsNone(task["result"])
        self.assertEqual(sum(shot["duration_seconds"] for shot in task["plan"]["shots"]), 120)
        self.assertEqual(task["plan"]["aspect_ratio"], "9:16")
        self.assertEqual(task["plan"]["constraints"]["fake_encounter"], False)
        self.assertEqual(task["plan"]["constraints"]["adult_body_face_paste"], False)
        bound = {item["asset_id"] for item in task["plan"]["references"]}
        self.assertEqual(bound, {item["asset_id"] for item in self.refs})
        self.assertEqual(task["fidelity"], "text_plan_not_a_film")
        self.assertEqual(self.calls[0][0], "POST")
        self.assertTrue(self.calls[0][1].endswith("/chat/completions"))
        encoded = json.dumps(self.calls[0][2]).encode("utf-8")
        self.assertNotIn(b"\x89PNG", encoded)
        self.assertNotIn(b"slate/direct", self.calls[0][1].encode())
        self.assertEqual(self.app.venice_transport.calls, [])
        with self.assertRaises(DirectorRefusal):
            self.app.read_result(task["id"])
        again = self.app.create_task(self._body())
        self.assertEqual(again["id"], task["id"])
        self.assertEqual(len(self.calls), 1)
        polled = self.app.poll(task["id"])
        self.assertEqual(polled["status"], "planned")
        self.assertEqual(len(self.calls), 1)

    def test_missing_brain_and_public_host_invent_nothing(self):
        self.app.brain_url = ""
        self.app.brain_model = ""
        task = self.app.create_task(self._body(client_request_id="plan-none"))
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "brain_not_configured")
        self.assertIsNone(task["plan"])
        self.assertEqual(self.calls, [])
        self.app.brain_url = "https://api.openai.com/v1"
        self.app.brain_model = "remote"
        refused = self.app.create_task(self._body(client_request_id="plan-public"))
        self.assertEqual(refused["error"]["code"], "brain_endpoint_rejected")
        self.assertIsNone(refused["plan"])
        self.assertEqual(self.calls, [])

    def test_bad_reply_is_not_replaced(self):
        self.plan = None
        task = self.app.create_task(self._body(client_request_id="plan-prose"))
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "plan_invalid")
        self.assertIsNone(task["plan"])
        self.assertNotIn("shots", task)
        short = self._valid()
        short["shots"] = short["shots"][:3]
        for index, shot in enumerate(short["shots"], start=1):
            shot["index"] = index
            shot["start_seconds"] = (index - 1) * 30
        self.plan = short
        failed = self.app.create_task(self._body(client_request_id="plan-short"))
        self.assertEqual(failed["error"]["code"], "plan_invalid")
        self.assertIn("not 120", failed["error"]["message"])
        self.assertIsNone(failed["plan"])
        bad = self._valid()
        bad["constraints"] = dict(CONSTRAINTS)
        bad["constraints"]["fake_encounter"] = True
        self.plan = bad
        flagged = self.app.create_task(self._body(client_request_id="plan-flag"))
        self.assertEqual(flagged["error"]["code"], "plan_invalid")
        self.assertIsNone(flagged["plan"])

    def test_style_reference_cannot_be_a_pictured_person(self):
        bad = self._valid()
        style_id = self.refs[3]["asset_id"]
        bad["shots"][0]["picture_asset_ids"] = bad["shots"][0]["picture_asset_ids"] + [style_id]
        bad["shots"][0]["style_asset_ids"] = []
        self.plan = bad
        task = self.app.create_task(self._body(client_request_id="plan-style"))
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "plan_invalid")
        self.assertIsNone(task["plan"])

    def test_empty_content_ignores_reasoning_and_draft_does_not_call(self):
        def transport(method, url, body, timeout):
            self.calls.append((method, url, body))
            return 200, {"choices": [{"message": {"content": "", "reasoning_content": json.dumps(self._valid())}}]}

        self.app.brain_transport = transport
        task = self.app.create_task(self._body(client_request_id="plan-empty"))
        self.assertEqual(task["error"]["code"], "plan_invalid")
        self.assertIsNone(task["plan"])
        draft = self.app.create_task(self._body(client_request_id="plan-draft", confirm=False))
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task(self._body(client_request_id="plan-120-missing", duration_seconds=90))
        self.assertEqual(caught.exception.code, "duration_rejected")


if __name__ == "__main__":
    unittest.main()
