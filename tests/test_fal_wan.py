"""MOCKED fal Wan 2.2 route tests. No test here calls fal.ai or generates a video."""

import shutil
import unittest
from pathlib import Path

from director.errors import DirectorRefusal
from director.service import DirectorApp
from test_director_contract import PNG_B64, MockTransport, Result, json_of

ROOT = Path(__file__).resolve().parents[1]
REQ = "req-123"
STATUS = "https://queue.fal.run/fal-ai/wan/requests/%s/status" % REQ
RESPONSE = "https://queue.fal.run/fal-ai/wan/requests/%s" % REQ


class FalWanTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-test-fal"
        shutil.rmtree(self.data, ignore_errors=True)
        self.fal = MockTransport()
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport(), fal_api_key="mock-fal-key", fal_transport=self.fal)

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)

    def submit_ok(self):
        self.fal.when("/fal-ai/wan/v2.2-a14b/", Result(200, {"request_id": REQ, "status_url": STATUS, "response_url": RESPONSE}))

    def test_capability_listed_first_and_ready_with_key(self):
        caps = self.app.capabilities()
        first = caps["capabilities"][0]
        self.assertEqual(first["id"], "video.fal_wan")
        self.assertEqual(first["readiness"]["status"], "ready_to_confirm")
        self.assertEqual(first["durations"], [5])
        self.assertEqual(caps["credentials"]["FAL_KEY"], "present")
        self.assertFalse(caps["generation_performed"])

    def test_missing_key_is_reported_and_nothing_sent(self):
        app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport(), fal_transport=self.fal)
        self.assertEqual(app.capabilities()["capabilities"][0]["readiness"]["status"], "credential_missing")
        with self.assertRaises(DirectorRefusal) as ctx:
            app.create_task({"capability": "video.fal_wan", "client_request_id": "k0", "prompt": "кот", "confirm": True})
        self.assertEqual(ctx.exception.code, "credential_missing")
        self.assertEqual(self.fal.calls, [])

    def test_without_confirm_only_a_draft(self):
        task = self.app.create_task({"capability": "video.fal_wan", "client_request_id": "d1", "prompt": "кот"})
        self.assertEqual(task["status"], "draft")
        self.assertEqual(task["params"]["duration_seconds"], 5)
        self.assertEqual(self.fal.calls, [])

    def test_text_to_video_is_fixed_5_seconds(self):
        self.submit_ok()
        task = self.app.create_task({"capability": "video.fal_wan", "client_request_id": "t1", "prompt": "закат над морем", "confirm": True})
        self.assertEqual(task["status"], "queued")
        call = self.fal.calls[0]
        self.assertEqual(call["url"], "https://queue.fal.run/fal-ai/wan/v2.2-a14b/text-to-video")
        self.assertEqual(call["headers"]["Authorization"], "Key mock-fal-key")
        body = json_of(call)
        self.assertEqual((body["num_frames"], body["frames_per_second"]), (81, 16))
        self.assertEqual(body["aspect_ratio"], "9:16")
        self.assertNotIn("image_url", body)

    def test_long_script_up_to_8000_chars_is_accepted(self):
        self.submit_ok()
        script = ("0–8 с. Девочка на кухне. «Ты решила рассказать обо всём маме.» " * 40)[:7900]
        self.assertGreater(len(script), 2000)
        task = self.app.create_task({"capability": "video.fal_wan", "client_request_id": "long1", "prompt": script, "confirm": True})
        self.assertEqual(task["status"], "queued")
        self.assertEqual(json_of(self.fal.calls[0])["prompt"], script.strip())
        with self.assertRaises(DirectorRefusal) as ctx:
            self.app.create_task({"capability": "video.fal_wan", "client_request_id": "long2", "prompt": "к" * 8001, "confirm": True})
        self.assertEqual(ctx.exception.code, "prompt_too_long")

    def test_photo_goes_to_image_to_video(self):
        asset = self.app.put_asset_json({"content_base64": PNG_B64, "media_type": "image/png"})
        self.submit_ok()
        self.app.create_task({"capability": "video.fal_wan", "client_request_id": "i1", "prompt": "оживи фото", "original_asset_id": asset["id"], "confirm": True})
        call = self.fal.calls[0]
        self.assertTrue(call["url"].endswith("/image-to-video"))
        self.assertTrue(json_of(call)["image_url"].startswith("data:image/png;base64,"))

    def test_longer_duration_refused_not_rewritten(self):
        with self.assertRaises(DirectorRefusal) as ctx:
            self.app.create_task({"capability": "video.fal_wan", "client_request_id": "l1", "prompt": "кот", "duration": 10, "confirm": True})
        self.assertEqual(ctx.exception.code, "duration_fixed")
        self.assertEqual(self.fal.calls, [])

    def test_retry_with_same_request_id_does_not_resubmit(self):
        self.submit_ok()
        body = {"capability": "video.fal_wan", "client_request_id": "r1", "prompt": "кот", "confirm": True}
        first = self.app.create_task(dict(body))
        second = self.app.create_task(dict(body))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.fal.calls), 1)

    def test_poll_queue_then_download(self):
        self.submit_ok()
        task = self.app.create_task({"capability": "video.fal_wan", "client_request_id": "p1", "prompt": "кот", "confirm": True})
        self.fal.routes = []
        self.fal.calls = []
        self.fal.when(STATUS, [Result(202, {"status": "IN_PROGRESS"}), Result(200, {"status": "COMPLETED"})])
        self.fal.when(RESPONSE, Result(200, {"video": {"url": "https://v3.fal.media/files/x.mp4"}}))
        self.fal.when("v3.fal.media", Result(200, b"MOCK-MP4-BYTES", "video/mp4"))
        self.assertEqual(self.app.poll(task["id"])["status"], "processing")
        done = self.app.poll(task["id"])
        self.assertEqual(done["status"], "completed")
        data, media = self.app.read_result(task["id"])
        self.assertEqual((data, media), (b"MOCK-MP4-BYTES", "video/mp4"))
        submits = [c for c in self.fal.calls if c["method"] == "POST"]
        self.assertEqual(submits, [])

    def test_provider_rejection_is_failed(self):
        self.fal.when("/fal-ai/wan/v2.2-a14b/", Result(401, {"detail": "bad key"}))
        task = self.app.create_task({"capability": "video.fal_wan", "client_request_id": "x1", "prompt": "кот", "confirm": True})
        self.assertEqual(task["status"], "failed")

    def test_poll_rejects_foreign_url(self):
        self.submit_ok()
        task = self.app.create_task({"capability": "video.fal_wan", "client_request_id": "u1", "prompt": "кот", "confirm": True})
        stored = self.app.store.get(task["id"])
        stored["fal_status_url"] = "https://evil.example/status"
        self.app.store.save(stored)
        with self.assertRaises(DirectorRefusal):
            self.app.poll(task["id"])


if __name__ == "__main__":
    unittest.main()


class FalFluxTest(unittest.TestCase):
    """MOCKED FLUX image route. Nothing is generated."""

    def setUp(self):
        self.data = ROOT / "director-data-test-flux"
        shutil.rmtree(self.data, ignore_errors=True)
        self.fal = MockTransport()
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport(), fal_api_key="mock-fal-key", fal_transport=self.fal)

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)

    def test_text_to_image_then_download(self):
        self.fal.when("/fal-ai/flux/dev", Result(200, {"request_id": "img1"}))
        task = self.app.create_task({"capability": "image.fal_flux", "client_request_id": "f1", "prompt": "мем с котом", "aspect_ratio": "1:1", "confirm": True})
        self.assertEqual(task["status"], "queued")
        body = json_of(self.fal.calls[0])
        self.assertEqual(body["image_size"], "square_hd")
        self.assertEqual(task["fal_status_url"], "https://queue.fal.run/fal-ai/flux/requests/img1/status")
        self.fal.routes = []
        self.fal.calls = []
        self.fal.when("/requests/img1/status", Result(200, {"status": "COMPLETED"}))
        self.fal.when("/requests/img1", Result(200, {"images": [{"url": "https://v3.fal.media/files/a.png"}]}))
        self.fal.when("v3.fal.media", Result(200, b"MOCK-PNG", "image/png"))
        done = self.app.poll(task["id"])
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.app.read_result(task["id"]), (b"MOCK-PNG", "image/png"))

    def test_photo_goes_to_kontext_edit(self):
        asset = self.app.put_asset_json({"content_base64": PNG_B64, "media_type": "image/png"})
        self.fal.when("/fal-ai/flux-kontext/dev", Result(200, {"request_id": "img2"}))
        self.app.create_task({"capability": "image.fal_flux", "client_request_id": "f2", "prompt": "карточка товара на белом фоне", "original_asset_id": asset["id"], "confirm": True})
        body = json_of(self.fal.calls[0])
        self.assertTrue(body["image_url"].startswith("data:image/png;base64,"))
        self.assertNotIn("image_size", body)
