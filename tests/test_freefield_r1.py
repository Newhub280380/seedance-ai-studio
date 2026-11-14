"""MOCKED Freefield apps in Режиссёр 1.0. No test here calls fal.ai or generates anything.

Payloads are checked against fal's published OpenAPI schemas saved 2026-11-14 in fixtures/freefield_fal_schemas."""

import base64
import json
import shutil
import unittest
from pathlib import Path

from director import freefield_apps as ff
from director import freefield_route
from director.server import DirectorHandler
from director.service import DirectorApp
from test_director_contract import MockTransport, Result, json_of

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = Path(__file__).resolve().parent / "fixtures" / "freefield_fal_schemas"
STATUS = "https://queue.fal.run/fal-ai/seedvr/requests/r1/status"
RESPONSE = "https://queue.fal.run/fal-ai/seedvr/requests/r1"
FILES = {"image": "a.png", "video": "a.mp4", "audio": "a.wav"}


def _schema(endpoint):
    doc = json.loads((SCHEMAS / (endpoint.replace("/", "_") + ".json")).read_text(encoding="utf-8"))
    comps = doc["components"]["schemas"]
    post = next(p["post"] for p in doc["paths"].values() if "post" in p)
    return comps[post["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]], comps


class FreefieldR1Test(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-test-freefield"
        shutil.rmtree(self.data, ignore_errors=True)
        self.fal = MockTransport()
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport(), fal_api_key="mock-fal-key", fal_transport=self.fal)
        self.app._freefield = freefield_route.FreefieldR1(self.app, sleep=lambda s: None)
        self.handler = DirectorHandler(self.app, ROOT)

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)

    def call(self, method, path, body=None):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        return self.handler.handle(method, path, raw, {})

    def upload(self, media="video/mp4", data=b"x" * 64):
        status, payload, _ = self.call("POST", "/api/director/freefield/uploads",
                                       {"media_type": media, "content_base64": base64.b64encode(data).decode()})
        self.assertEqual(status, 200)
        return payload["filename"]

    def test_catalog_lists_ready_and_soon_apps(self):
        status, payload, _ = self.call("GET", "/api/director/freefield/apps")
        self.assertEqual(status, 200)
        apps = {a["id"]: a for a in payload["apps"]}
        for key in ("recast", "motion", "dub", "speak", "talking", "upscale-video"):
            self.assertIn(key, apps)
        self.assertEqual(apps["dub"]["status"], "soon")
        self.assertTrue(payload["fal_ready"])
        self.assertTrue(all(a["needs"] for a in apps.values() if a["status"] == "soon"))
        self.assertEqual(self.fal.calls, [])

    def test_every_ready_app_matches_fal_schema(self):
        names = {kind: self.upload({"image": "image/png", "video": "video/mp4", "audio": "audio/wav"}[kind]) for kind in FILES}
        for app in [a for a in ff.APPS if a["status"] == "ready"]:
            values = {}
            for spec in app["inputs"]:
                if spec["type"] in FILES:
                    values[spec["id"]] = names[spec["type"]]
                elif spec["required"] and "default" not in spec:
                    values[spec["id"]] = "test"
            status, plan, _ = self.call("POST", "/api/director/freefield/apps/%s/dry-run" % app["id"], {"inputs": values})
            self.assertEqual(status, 200, (app["id"], plan))
            self.assertFalse(plan["submitted"])
            schema, comps = _schema(app["endpoint"])
            for key in schema.get("required", []):
                self.assertIn(key, plan["body"], app["id"])
            for key, value in plan["body"].items():
                self.assertIn(key, schema["properties"], (app["id"], key))
                prop = schema["properties"][key]
                allowed = prop.get("enum") or (comps.get(prop["allOf"][0].get("$ref", "").split("/")[-1], {}).get("enum") if prop.get("allOf") else None)
                if allowed:
                    self.assertIn(value, allowed, (app["id"], key))
        self.assertEqual(self.fal.calls, [])

    def test_run_needs_confirm_and_shows_price(self):
        clip = self.upload()
        status, payload, _ = self.call("POST", "/api/director/freefield/apps/upscale-video/run", {"inputs": {"clip": clip}})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "confirm_required")
        self.assertIn("$0.001", payload["error"]["message"])
        self.assertEqual(self.fal.calls, [])

    def test_soon_app_and_path_escape_are_refused(self):
        status, payload, _ = self.call("POST", "/api/director/freefield/apps/dub/dry-run", {"inputs": {}})
        self.assertEqual(status, 400)
        self.assertIn("скоро", payload["error"]["message"])
        status, _, _ = self.call("POST", "/api/director/freefield/apps/upscale-video/dry-run", {"inputs": {"clip": "..\\..\\.env"}})
        self.assertEqual(status, 400)
        status, _, _ = self.call("POST", "/api/director/freefield/uploads", {"media_type": "text/plain", "content_base64": "eA=="})
        self.assertEqual(status, 400)

    def test_confirmed_run_with_mocked_fal(self):
        clip = self.upload()
        self.fal.when("queue.fal.run/fal-ai/seedvr/upscale/video", Result(200, {"request_id": "r1", "status_url": STATUS, "response_url": RESPONSE}))
        self.fal.when(STATUS, Result(200, {"status": "COMPLETED"}))
        self.fal.when(RESPONSE, Result(200, {"video": {"url": "https://v3.fal.media/files/out.mp4"}}))
        self.fal.when("v3.fal.media/files/out.mp4", Result(200, b"MP4DATA", "video/mp4"))
        state = self.app._freefield.run("upscale-video", {"inputs": {"clip": clip, "target": "2160p"}, "confirm": True}, background=False)
        self.assertEqual(state["status"], "done", state)
        sent = json_of(self.fal.calls[0])
        self.assertEqual(sent["target_resolution"], "2160p")
        self.assertTrue(sent["video_url"].startswith("data:video/mp4;base64,"))
        status, data, media = self.call("GET", state["output"]["url"])
        self.assertEqual((status, data, media), (200, b"MP4DATA", "video/mp4"))


if __name__ == "__main__":
    unittest.main()
