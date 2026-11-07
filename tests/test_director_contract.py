"""MOCKED provider contract tests.

No test in this module calls Venice, MiniMax, Hugging Face or a GPU.
Success means the recorded mock response was stored. It is not a generated image or video.
"""

import base64
import hashlib
import json
import subprocess
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from director.errors import DirectorRefusal, TransportFailure
from director.pngutil import solid_png
from director.server import DirectorHandler
from director.service import DirectorApp

ROOT = Path(__file__).resolve().parents[1]
PNG = solid_png()
PNG_B64 = base64.b64encode(PNG).decode("ascii")


class Result:
    def __init__(self, status, body, content_type="application/json"):
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.headers = {"content-type": content_type}


class MockTransport:
    def __init__(self):
        self.calls = []
        self.routes = []

    def when(self, fragment, result):
        self.routes.append((fragment, result))
        return self

    def request(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        for fragment, result in self.routes:
            if fragment in url:
                if isinstance(result, list):
                    if not result:
                        raise AssertionError("no scripted response left for %s" % url)
                    result = result.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError("unexpected mocked call %s %s" % (method, url))


def paths(transport):
    return [call["url"] for call in transport.calls]


def json_of(call):
    return json.loads(call["body"].decode("utf-8"))


class DirectorContractTest(unittest.TestCase):
    def setUp(self):
        self.venice = MockTransport()
        self.minimax = MockTransport()
        self.data = ROOT / "director-data-test"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.app = DirectorApp(
            ROOT,
            self.data,
            self.venice,
            self.minimax,
            venice_api_key="mock-venice-key",
            minimax_api_key="mock-minimax-key",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.data, ignore_errors=True)

    def asset(self):
        return self.app.solid_fixture()["id"]

    def test_skill_hashes_and_curated_catalog_not_full_list(self):
        payload = self.app.capabilities()
        self.assertEqual(payload["catalog"]["full_catalog_counts_this_fetch"]["video"], 138)
        self.assertLess(len(payload["capabilities"]), 30)
        skills = [item for item in payload["capabilities"] if item["id"] == "image.create"][0]["skills"]
        self.assertTrue(all(skill["sha256_ok"] and not skill["missing_endpoints"] for skill in skills))
        self.assertTrue(all(skill["executes_backend"] is False for skill in skills))
        self.assertNotIn("mock-venice-key", json.dumps(payload))

    def test_ui_client_reaches_mocked_generate(self):
        self.venice.when("/api/v1/image/generate", Result(200, {"id": "mock-gen", "images": [PNG_B64]}, "application/json"))
        body = json.loads(subprocess.check_output([
            "node", "-e",
            "const c=require('./director/ui/director-client.js');"
            "process.stdout.write(JSON.stringify(c.buildTaskBody({"
            "capability:'image.create', clientRequestId:'ui-1', confirm:true,"
            "model:'flux-2-pro', prompt:'A flat blue square', aspect_ratio:'1:1'"
            "})))",
        ], cwd=ROOT))
        handler = DirectorHandler(self.app, ROOT)
        httpd = self._serve(handler)
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:%s/api/director/tasks" % httpd.server_address[1],
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
            page = urllib.request.urlopen("http://127.0.0.1:%s/index.html" % httpd.server_address[1])
            html = page.read().decode("utf-8")
        finally:
            httpd.shutdown()
        self.assertEqual(payload["task"]["status"], "completed")
        self.assertEqual(payload["task"]["model"], "flux-2-pro")
        self.assertIn('id="gallery"', html)
        self.assertIn('id="directorBtn"', html)
        sent = json_of(self.venice.calls[0])
        self.assertEqual(sent["model"], "flux-2-pro")
        self.assertNotIn("modelId", sent)
        self.assertEqual(self.venice.calls[0]["headers"]["Authorization"], "Bearer mock-venice-key")
        again = self.app.create_task(body)
        self.assertEqual(again["id"], payload["task"]["id"])
        self.assertEqual(len(self.venice.calls), 1)
        stored = self.app.read_result(payload["task"]["id"])[0]
        self.assertEqual(hashlib.sha256(stored).hexdigest(), payload["task"]["result"]["sha256"])

    def test_product_multiref_keeps_original_and_rejects_extra(self):
        original = self.asset()
        references = [self.asset(), self.asset()]
        self.venice.when("/api/v1/image/multi-edit", Result(200, PNG, "image/png"))
        task = self.app.create_task({
            "capability": "image.product_multiref",
            "client_request_id": "product-1",
            "confirm": True,
            "prompt": "Place the product on a marble counter",
            "original_asset_id": original,
            "reference_asset_ids": references,
            "aspect_ratio": "1:1",
        })
        sent = json_of(self.venice.calls[0])
        self.assertEqual(sent["modelId"], "firered-image-edit")
        self.assertNotIn("model", sent)
        self.assertIn("Keep the product, its label and colors unchanged.", sent["prompt"])
        self.assertEqual(task["params"]["sent_asset_sha256"][0], original)
        self.assertEqual(_sha(sent["images"][0]), original)
        self.assertEqual(_sha(sent["images"][1]), references[0])
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "image.product_multiref",
                "client_request_id": "product-too-many",
                "confirm": True,
                "prompt": "Another scene",
                "original_asset_id": original,
                "reference_asset_ids": [self.asset(), self.asset(), self.asset()],
            })
        self.assertEqual(caught.exception.code, "reference_cap_exceeded")
        self.assertEqual(caught.exception.task["status"], "refused")
        self.assertEqual(len(self.venice.calls), 1)

    def test_explicit_nano_cap_is_not_a_silent_upgrade(self):
        original = self.asset()
        refs = [self.asset() for _ in range(3)]
        self.venice.when("/api/v1/image/multi-edit", Result(200, PNG, "image/png"))
        task = self.app.create_task({
            "capability": "image.product_multiref",
            "client_request_id": "product-nano",
            "confirm": True,
            "model": "nano-banana-2-edit",
            "prompt": "Lifestyle scene",
            "original_asset_id": original,
            "reference_asset_ids": refs,
        })
        self.assertEqual(task["status"], "completed")
        self.assertEqual(json_of(self.venice.calls[0])["modelId"], "nano-banana-2-edit")

    def test_reference_video_quote_poll_and_rejects_camera_and_face(self):
        image = self.asset()
        self.venice.when("/api/v1/video/quote", Result(200, {"quote": 0.55}))
        self.venice.when("/api/v1/video/queue", Result(200, {"model": "seedance-2-5-reference-to-video-basic", "queue_id": "q-mock-1"}))
        quoted = self.app.create_task({
            "capability": "video.reference",
            "client_request_id": "video-1",
            "confirm": False,
            "prompt": "The product turns on a table",
            "duration": "5s",
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "original_asset_id": image,
            "reference_asset_ids": [self.asset()],
        })
        self.assertEqual(quoted["status"], "quoted")
        self.assertEqual(paths(self.venice), ["https://api.venice.ai/api/v1/video/quote"])
        queued = self.app.confirm(quoted["id"])
        self.assertEqual(queued["provider_task_id"], "q-mock-1")
        self.assertEqual(paths(self.venice).count("https://api.venice.ai/api/v1/video/queue"), 1)
        self.venice.routes.append(("/api/v1/video/retrieve", [
            Result(200, {"status": "PROCESSING", "average_execution_time": 1000, "execution_duration": 10}),
            Result(200, b"MOCK-MP4", "video/mp4"),
        ]))
        processing = self.app.poll(queued["id"])
        self.assertEqual(processing["status"], "processing")
        done = self.app.poll(queued["id"])
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.app.read_result(done["id"])[0], b"MOCK-MP4")
        self.assertEqual(paths(self.venice).count("https://api.venice.ai/api/v1/video/queue"), 1)
        again = self.app.confirm(done["id"])
        self.assertEqual(again["status"], "completed")
        self.assertEqual(paths(self.venice).count("https://api.venice.ai/api/v1/video/queue"), 1)
        with self.assertRaises(DirectorRefusal) as camera:
            self.app.create_task({
                "capability": "video.reference",
                "client_request_id": "video-camera",
                "prompt": "orbit",
                "duration": "5s",
                "aspect_ratio": "16:9",
                "original_asset_id": image,
                "camera_trajectory": [{"time": 0, "azimuth": 0, "elevation": 0, "distance": 1}, {"time": 1, "azimuth": 20, "elevation": 0, "distance": 1}],
            })
        self.assertEqual(camera.exception.code, "camera_mode_unsupported")
        with self.assertRaises(DirectorRefusal) as face:
            self.app.create_task({
                "capability": "image.edit",
                "client_request_id": "face",
                "prompt": "restore",
                "original_asset_id": image,
                "identity_mode": "face_restore",
            })
        self.assertEqual(face.exception.code, "identity_mode_unsupported")

    def test_uncertain_submit_is_not_retried_and_failed_task_remains(self):
        image = self.asset()
        self.venice.when("/api/v1/video/quote", Result(200, {"quote": 0.2}))
        self.venice.when("/api/v1/video/queue", TransportFailure("timeout", uncertain=True))
        task = self.app.create_task({
            "capability": "video.reference",
            "client_request_id": "uncertain-1",
            "confirm": True,
            "prompt": "A cup on a table",
            "duration": "5s",
            "aspect_ratio": "16:9",
            "original_asset_id": image,
        })
        self.assertEqual(task["status"], "submit_uncertain")
        calls = len(self.venice.calls)
        again = self.app.confirm(task["id"])
        self.assertEqual(again["status"], "submit_uncertain")
        self.assertEqual(len(self.venice.calls), calls)
        self.venice.routes = [
            ("/api/v1/video/quote", Result(200, {"quote": 0.2})),
            ("/api/v1/video/queue", Result(500, {"error": "mock failure"})),
        ]
        failed = self.app.create_task({
            "capability": "video.reference",
            "client_request_id": "failed-1",
            "confirm": True,
            "prompt": "A cup on a table",
            "duration": "5s",
            "aspect_ratio": "16:9",
            "original_asset_id": image,
        })
        self.assertEqual(failed["status"], "failed")
        self.assertIsNotNone(self.app.store.get(failed["id"]))

    def test_cancel_before_queue_does_not_queue(self):
        self.venice.when("/api/v1/video/quote", Result(200, {"quote": 0.1}))
        task = self.app.create_task({
            "capability": "video.reference",
            "client_request_id": "cancel-1",
            "prompt": "A still life",
            "duration": "5s",
            "aspect_ratio": "16:9",
            "original_asset_id": self.asset(),
        })
        cancelled = self.app.cancel(task["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertNotIn("https://api.venice.ai/api/v1/video/queue", paths(self.venice))

    def test_missing_venice_key_sends_nothing(self):
        self.app.venice_api_key = ""
        self.app.venice.api_key = ""
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "image.create",
                "client_request_id": "nokey",
                "confirm": True,
                "prompt": "A square",
                "aspect_ratio": "1:1",
            })
        self.assertEqual(caught.exception.code, "credential_missing")
        self.assertEqual(self.venice.calls, [])
        self.assertEqual(caught.exception.task["status"], "draft")

    def test_explainer_quotes_all_and_queues_first_scene_only(self):
        self.venice.when("/api/v1/video/quote", Result(200, {"quote": 0.4}))
        self.venice.when("/api/v1/audio/speech", Result(200, b"MOCK-MP3", "audio/mpeg"))
        self.venice.when("/api/v1/video/queue", Result(200, {"queue_id": "scene-q", "model": "wan-2.5-preview-text-to-video"}))
        project = self.app.create_task({
            "capability": "explainer.project",
            "client_request_id": "explain-1",
            "confirm": False,
            "aspect_ratio": "16:9",
            "resolution": "720p",
            "voice": "af_sky",
            "scenes": [
                {"prompt": "A diagram appears", "narration": "First point.", "duration": "5s"},
                {"prompt": "The diagram closes", "narration": "Second point.", "duration": "5s"},
            ],
        })
        self.assertEqual(project["status"], "quoted")
        self.assertEqual(paths(self.venice).count("https://api.venice.ai/api/v1/video/quote"), 2)
        self.assertNotIn("https://api.venice.ai/api/v1/video/queue", paths(self.venice))
        started = self.app.confirm(project["id"])
        self.assertEqual(started["status"], "awaiting_remaining_scenes")
        self.assertIsNotNone(started["params"]["scenes"][0]["video_task_id"])
        self.assertIsNone(started["params"]["scenes"][1].get("video_task_id"))
        self.assertEqual(started["assembly"]["status"], "not_run")
        queue_body = json_of([call for call in self.venice.calls if call["url"].endswith("/video/queue")][0])
        self.assertNotIn("audio", queue_body)
        continued = self.app.confirm(started["id"], continue_remaining=True)
        self.assertIsNotNone(continued["params"]["scenes"][1]["video_task_id"])
        self.assertEqual(paths(self.venice).count("https://api.venice.ai/api/v1/video/queue"), 2)

    def test_h3_lanes_do_not_fall_into_each_other(self):
        payload = self.app.capabilities()
        by_id = {item["id"]: item for item in payload["capabilities"]}
        base = by_id["h3.base_fl2va_original"]
        self.assertTrue(base["published_weights"])
        self.assertFalse(base["weights_downloaded_by_director"])
        self.assertFalse(base["downloadable"])
        closed = by_id["h3.context_ir_hosted"]
        regen = by_id["h3.regenerate_2k_hosted"]
        self.assertFalse(closed["published_weights"])
        self.assertFalse(closed["downloadable"])
        self.assertFalse(regen["downloadable"])
        comfy = by_id["comfy.h3_base_ref2va_quant_turbo"]
        self.assertIn("minimax_h3_ref2va_pruned_int8_convrot.safetensors", comfy["weight_filenames"])
        self.assertFalse(comfy["labels"]["is_h3_max"])
        self.assertFalse(comfy["labels"]["is_context_ir"])
        self.assertFalse(comfy["labels"]["is_regenerate_2k"])
        self.assertEqual(comfy["commercial_runtime"], "disabled")
        refused = self.app.create_task({"capability": "comfy.h3_base_fl2va_quant_turbo", "client_request_id": "comfy-1", "provider": "comfy"})
        self.assertEqual(refused["status"], "refused")
        context = self.app.create_task({"capability": "h3.context_ir_hosted", "client_request_id": "ctx-1", "provider": "none"})
        self.assertEqual(context["status"], "refused")
        self.assertEqual(self.venice.calls, [])
        self.assertEqual(self.minimax.calls, [])
        with self.assertRaises(DirectorRefusal) as two_k:
            self.app.create_task({
                "capability": "video.h3_max_hosted",
                "client_request_id": "max-2k",
                "confirm": True,
                "prompt": "A tram crosses a bridge",
                "resolution": "2K",
                "duration": 5,
                "ratio": "16:9",
                "prompt_expansion_mode": "disabled",
            })
        self.assertEqual(two_k.exception.code, "resolution_not_supported_no_fallback")
        self.assertEqual(self.minimax.calls, [])
        with self.assertRaises(DirectorRefusal) as framed:
            self.app.create_task({
                "capability": "video.h3_max_hosted",
                "client_request_id": "max-frame",
                "confirm": True,
                "prompt": "Move closer",
                "resolution": "768P",
                "duration": 5,
                "ratio": "16:9",
                "prompt_expansion_mode": "balanced",
                "original_asset_id": self.asset(),
            })
        self.assertEqual(framed.exception.code, "ratio_must_be_adaptive")
        self.minimax.when("/v2/video_generation", Result(200, {"task_id": "mm-task-1"}))
        self.minimax.when("/v2/query/video_generation/", Result(200, {"task": {"id": "mm-task-1", "status": "succeeded", "content": {"url": "https://cdn.example.test/mock.mp4"}}}))
        self.minimax.when("https://cdn.example.test/mock.mp4", Result(200, b"MOCK-MAX", "video/mp4"))
        queued = self.app.create_task({
            "capability": "video.h3_max_hosted",
            "client_request_id": "max-1",
            "confirm": True,
            "prompt": "A tram crosses a bridge",
            "resolution": "768P",
            "duration": 5,
            "ratio": "16:9",
            "prompt_expansion_mode": "balanced",
        })
        self.assertEqual(queued["provider_task_id"], "mm-task-1")
        sent = json_of(self.minimax.calls[0])
        self.assertEqual(sent["model"], "MiniMax-H3-Max")
        self.assertEqual(sent["resolution"], "768P")
        self.assertNotEqual(sent["model"], "MiniMax-H3")
        done = self.app.poll(queued["id"])
        self.assertEqual(done["status"], "completed")
        self.assertEqual(self.app.read_result(done["id"])[0], b"MOCK-MAX")
        creates = [call for call in self.minimax.calls if call["url"].endswith("/v2/video_generation")]
        self.assertEqual(len(creates), 1)

    def test_provider_mismatch_is_not_redirected(self):
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "video.h3_max_hosted",
                "provider": "venice",
                "client_request_id": "mix",
                "prompt": "no",
            })
        self.assertEqual(caught.exception.code, "provider_mismatch")
        self.assertEqual(self.venice.calls, [])
        self.assertEqual(self.minimax.calls, [])

    def test_poll_budget_stops_without_resubmit(self):
        self.venice.when("/api/v1/video/quote", Result(200, {"quote": 0.3}))
        self.venice.when("/api/v1/video/queue", Result(200, {"queue_id": "q-budget"}))
        self.venice.when("/api/v1/video/retrieve", Result(200, {"status": "PROCESSING"}))
        task = self.app.create_task({
            "capability": "video.reference",
            "client_request_id": "budget",
            "confirm": True,
            "prompt": "A lamp",
            "duration": "5s",
            "aspect_ratio": "16:9",
            "original_asset_id": self.asset(),
        })
        for _ in range(6):
            task = self.app.poll(task["id"])
        stopped = self.app.poll(task["id"])
        self.assertEqual(stopped["error"]["code"], "poll_budget_exhausted")
        self.assertEqual(paths(self.venice).count("https://api.venice.ai/api/v1/video/queue"), 1)
        self.assertEqual(paths(self.venice).count("https://api.venice.ai/api/v1/video/retrieve"), 6)

    def _serve(self, handler):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self._go("GET", b"")

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self._go("POST", self.rfile.read(length) if length else b"")

            def _go(self, method, raw):
                status, payload, media = handler.handle(method, self.path, raw, self.headers)
                data = json.dumps(payload).encode("utf-8") if isinstance(payload, (dict, list)) else payload
                self.send_response(status)
                self.send_header("Content-Type", media if isinstance(payload, bytes) else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        owner.addCleanup(httpd.shutdown)
        owner.addCleanup(httpd.server_close)
        return httpd


def _sha(data_url):
    encoded = data_url.split(",", 1)[1]
    return hashlib.sha256(base64.b64decode(encoded)).hexdigest()


if __name__ == "__main__":
    unittest.main()
