"""MOCK Studio socket. Not a GPU render and not a generated portrait.

The local server only records the upload and the job JSON. Contract tests
check that the prepared image bytes are in the multipart body and that the
job points at the returned path. upstream_accepted is not a finished file.
"""

import json
import os
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from director.errors import DirectorRefusal
from director.pngutil import solid_png
from director.server import DirectorHandler
from director.service import DirectorApp
from director.studio_route import BEAUTY_RETOUCH_PROMPT_EXAMPLE, PRODUCTION_IDENTITY, VERSION
from tests.test_director_contract import MockTransport, ROOT
from tests.test_prep_photo import _write_mock


def _probe(edit_ready=True, ref2va=False, models=("qwen",)):
    return {
        "reachable": True,
        "error": "",
        "health": {"session": ""},
        "updates": {"automatic": False, "managed": False, "installed_version": VERSION},
        "swap": {"ready": False, "why": "not this test"},
        "actions": {"remove": {"ready": False, "why": "not this test"}},
        "revise": {"kinds": []},
        "brains": {"default": "local"},
        "state": {
            "version": VERSION,
            "platform_label": "mock",
            "gpu_index": None,
            "ref2va": ref2va,
            "engine": "none",
            "image_models": [{"id": item} for item in models],
            "reference_limit": 9,
        },
        "edit": {
            "ready": edit_ready,
            "why": "" if edit_ready else "Qwen-Image-2.1-Turbo is not installed here",
            "models": [{"id": item} for item in models],
        },
    }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _raw(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        raw = self._raw()
        box = self.server.box
        if self.path == "/api/upload":
            box["uploads"].append(raw)
            self._send(200, {"path": box["upload_path"]})
            return
        if self.path == "/api/jobs":
            box["jobs"].append(json.loads(raw.decode("utf-8")))
            self._send(200, {"id": "job-ref", "status": "queued", "outputs": []})
            return
        box["other"].append(self.path)
        self._send(404, {"error": "unexpected %s" % self.path})

    def do_GET(self):
        box = self.server.box
        box["gets"].append(self.path)
        if self.path.startswith("/api/jobs/"):
            self._send(200, {
                "id": "job-ref",
                "status": box["job_status"],
                "outputs": box["outputs"],
                "error": box.get("job_error"),
            })
            return
        if self.path.startswith("/files/"):
            blob = box.get("file")
            if not blob:
                self._send(404, b"")
                return
            self._send(200, blob, "application/octet-stream")
            return
        self._send(404, {"error": "no"})


class StudioReferenceTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-ref-test"
        if self.data.exists():
            shutil.rmtree(self.data)
        self.data.mkdir()
        self.mock = self.data / "mock-prep"
        _write_mock(self.mock)
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport())
        self.app.prep_photo_command = str(self.mock)
        self.app.prep_timeout = 30
        os.environ["MOCK_PREP_MODE"] = "ok"
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.box = {
            "uploads": [],
            "jobs": [],
            "gets": [],
            "other": [],
            "upload_path": "uploads/prepared.jpg",
            "job_status": "queued",
            "outputs": [],
            "file": None,
        }
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.app.studio_url = "http://127.0.0.1:%s" % self.httpd.server_address[1]
        self.app.studio_transport = None
        self.app._studio_probe_cached = _probe()

    def tearDown(self):
        os.environ.pop("MOCK_PREP_MODE", None)
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()
        shutil.rmtree(self.data, ignore_errors=True)

    def _prep(self):
        asset = self.app.assets.put(solid_png(), "image/png")
        task = self.app.create_task({
            "capability": "image.subject_prep",
            "client_request_id": "ref-prep",
            "confirm": True,
            "subject": "person",
            "original_asset_id": asset["id"],
        })
        self.assertEqual(task["status"], "completed", task.get("error"))
        return task

    def test_prepared_jpeg_bytes_reach_edit_job_and_file_gates_completion(self):
        """Not a GPU render. The socket stands in for Studio and returns a canned PNG."""
        prep = self._prep()
        jpeg = self.app.read_result(prep["id"], role="jpeg_white")[0]
        png = self.app.read_result(prep["id"], role="output")[0]
        original = prep["prep"]["original_sha256"]
        task = self.app.create_task({
            "capability": "studio.image_edit",
            "client_request_id": "ref-edit",
            "confirm": True,
            "prep_task_id": prep["id"],
            "reference_role": "jpeg_white",
            "image_model": "qwen",
            "prompt": BEAUTY_RETOUCH_PROMPT_EXAMPLE,
        })
        self.assertEqual(task["status"], "upstream_accepted", task.get("error"))
        self.assertIsNone(task["result"])
        self.assertTrue(task["prep_hashes_unchanged"])
        self.assertEqual(task["params"]["prompt_source"], "request")
        self.assertFalse(task["params"]["beauty_retouch_applied_by_default"])
        self.assertFalse(task["params"]["medical_claim"])
        self.assertFalse(task["params"]["needs_h3"])
        self.assertIn(jpeg, self.httpd.box["uploads"][0])
        self.assertNotIn(b"Comfy", self.httpd.box["uploads"][0])
        job = self.httpd.box["jobs"][0]
        self.assertEqual(job["kind"], "edit")
        self.assertEqual(job["params"]["image"], "uploads/prepared.jpg")
        self.assertEqual(job["params"]["prompt"], BEAUTY_RETOUCH_PROMPT_EXAMPLE)
        self.assertEqual(job["params"]["image_model"], "qwen")
        self.assertNotIn("references", job["params"])
        self.assertNotIn("/api/slate/direct", self.httpd.box["other"])
        self.assertEqual(self.app.venice_transport.calls, [])
        with self.assertRaises(DirectorRefusal) as missing:
            self.app.read_result(task["id"])
        self.assertEqual(missing.exception.code, "no_result")
        waiting = self.app.poll(task["id"])
        self.assertEqual(waiting["status"], "upstream_accepted")
        self.assertEqual(len(self.httpd.box["jobs"]), 1)
        self.httpd.box["job_status"] = "done"
        self.httpd.box["outputs"] = []
        empty = self.app.poll(task["id"])
        self.assertEqual(empty["status"], "upstream_accepted")
        self.assertEqual(empty["error"]["code"], "artifact_not_ready")
        self.assertIsNone(empty.get("result"))
        self.httpd.box["outputs"] = ["scouts/job-ref/edit.png"]
        self.httpd.box["file"] = b"not-an-image"
        invalid = self.app.poll(task["id"])
        self.assertEqual(invalid["status"], "failed")
        self.assertEqual(invalid["error"]["code"], "artifact_invalid")
        self.assertEqual(self.app.read_result(prep["id"], role="jpeg_white")[0], jpeg)
        self.assertEqual(self.app.read_result(prep["id"], role="output")[0], png)
        self.assertEqual(self.app.assets.get(original)["sha256"], original)
        listed = [item for item in self.app.capabilities()["capabilities"] if item["id"] == "studio.image_edit"][0]
        self.assertFalse(listed["needs_h3"])
        self.assertEqual(listed["readiness"]["status"], "ready_to_confirm")

    def test_png_master_is_uploaded_unchanged_and_completion_stores_a_separate_file(self):
        prep = self._prep()
        png = self.app.read_result(prep["id"], role="output")[0]
        self.httpd.box["upload_path"] = "uploads/prepared.png"
        task = self.app.create_task({
            "capability": "studio.image_edit",
            "client_request_id": "ref-png",
            "confirm": True,
            "prep_task_id": prep["id"],
            "reference_role": "output",
            "prompt": "Edit image 1 only. Keep the person.",
        })
        self.assertEqual(task["status"], "upstream_accepted", task.get("error"))
        self.assertIn(png, self.httpd.box["uploads"][0])
        self.assertTrue(png.startswith(b"\x89PNG"))
        rendered = solid_png(12, 12, (9, 9, 9))
        self.httpd.box["job_status"] = "done"
        self.httpd.box["outputs"] = ["scouts/job-ref/edit.png"]
        self.httpd.box["file"] = rendered
        done = self.app.poll(task["id"])
        self.assertEqual(done["status"], "completed", done.get("error"))
        stored, media = self.app.read_result(done["id"])
        self.assertEqual(stored, rendered)
        self.assertEqual(media, "image/png")
        self.assertNotEqual(stored, png)
        self.assertTrue(done["result"]["separate_from_prep"])
        self.assertEqual(self.app.read_result(prep["id"], role="output")[0], png)
        again = self.app.poll(done["id"])
        self.assertEqual(again["status"], "completed")
        self.assertEqual(len([path for path in self.httpd.box["gets"] if path.startswith("/files/")]), 1)

    def test_prompt_only_missing_renderer_and_clinical_fields_do_not_upload(self):
        prep = self._prep()
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "studio.image_edit",
                "client_request_id": "ref-noprompt",
                "confirm": True,
                "prep_task_id": prep["id"],
                "beauty_retouch": True,
            })
        self.assertEqual(caught.exception.code, "prompt_required")
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "studio.image_edit",
                "client_request_id": "ref-noref",
                "confirm": True,
                "prompt": BEAUTY_RETOUCH_PROMPT_EXAMPLE,
            })
        self.assertEqual(caught.exception.code, "reference_required")
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "studio.image_edit",
                "client_request_id": "ref-dose",
                "confirm": True,
                "prep_task_id": prep["id"],
                "prompt": "Edit image 1.",
                "dose_ml": 1,
            })
        self.assertEqual(caught.exception.code, "clinical_protocol_rejected")
        self.app._studio_probe_cached = _probe(edit_ready=False)
        missing = self.app.create_task({
            "capability": "studio.image_edit",
            "client_request_id": "ref-not-ready",
            "confirm": True,
            "prep_task_id": prep["id"],
            "prompt": "Edit image 1.",
        })
        self.assertEqual(missing["status"], "failed")
        self.assertEqual(missing["error"]["code"], "studio_renderer_unavailable")
        self.assertEqual(self.httpd.box["uploads"], [])
        self.assertEqual(self.httpd.box["jobs"], [])
        self.assertEqual(self.app.venice_transport.calls, [])

    def test_same_request_and_confirm_do_not_upload_twice(self):
        prep = self._prep()
        body = {
            "capability": "studio.image_edit",
            "client_request_id": "ref-once",
            "confirm": True,
            "prep_task_id": prep["id"],
            "reference_role": "jpeg_white",
            "prompt": "Edit image 1 only.",
        }
        first = self.app.create_task(body)
        second = self.app.create_task(body)
        self.assertEqual(first["id"], second["id"])
        self.app.confirm(first["id"])
        self.assertEqual(len(self.httpd.box["uploads"]), 1)
        self.assertEqual(len(self.httpd.box["jobs"]), 1)

    def test_reference_video_needs_ref2va_and_sends_the_file_as_references(self):
        prep = self._prep()
        jpeg = self.app.read_result(prep["id"], role="jpeg_white")[0]
        blocked = self.app.create_task({
            "capability": "studio.reference_video",
            "client_request_id": "ref-video-blocked",
            "confirm": True,
            "prep_task_id": prep["id"],
            "prompt": "Use <Picture 1> as the person.",
        })
        self.assertEqual(blocked["error"]["code"], "studio_renderer_unavailable")
        self.assertEqual(self.httpd.box["uploads"], [])
        self.app._studio_probe_cached = _probe(ref2va=True)
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "studio.reference_video",
                "client_request_id": "ref-video-tag",
                "confirm": True,
                "prep_task_id": prep["id"],
                "prompt": "A person walks.",
            })
        self.assertEqual(caught.exception.code, "picture_tag_required")
        task = self.app.create_task({
            "capability": "studio.reference_video",
            "client_request_id": "ref-video",
            "confirm": True,
            "prep_task_id": prep["id"],
            "seconds": 5,
            "prompt": "Use <Picture 1> as the person. The person in <Picture 1> looks aside.",
        })
        self.assertEqual(task["status"], "upstream_accepted", task.get("error"))
        self.assertTrue(task["params"]["needs_h3"])
        self.assertIn(jpeg, self.httpd.box["uploads"][0])
        job = self.httpd.box["jobs"][0]
        self.assertEqual(job["kind"], "video")
        self.assertEqual(job["params"]["references"], ["uploads/prepared.jpg"])
        self.assertNotIn("image", job["params"])
        self.assertEqual(job["params"]["seconds"], 5)

    def test_preflight_is_get_only_and_has_no_default_portrait(self):
        from director.preflight import collect
        calls = []

        def transport(method, url, body, timeout):
            calls.append(method)
            if method != "GET":
                raise AssertionError(method)
            if url.endswith("/api/edit/state"):
                return 200, {"ready": False, "why": "not installed", "models": []}
            if url.endswith("/api/state"):
                return 200, {"version": VERSION, "ref2va": False, "image_models": [], "platform_label": "mock", "gpu_index": None, "engine": "none"}
            return 200, {"automatic": False, "managed": False, "ready": False, "why": "mock", "default": "local", "kinds": []}

        report = collect(ROOT, self.data / "preflight", transport)
        self.assertTrue(report["dry_preflight"])
        self.assertFalse(report["gpu_tested"])
        self.assertFalse(report["jobs_submitted"])
        self.assertEqual(report["production_identity"], PRODUCTION_IDENTITY)
        self.assertIsNone(report["routes"]["studio_image_edit"]["prompt_default"])
        self.assertFalse(report["routes"]["studio_image_edit"]["requires_h3"])
        self.assertTrue(report["routes"]["studio_reference_video"]["requires_h3"])
        self.assertEqual(report["examples"]["comfy_sample"], "not_a_payload")
        self.assertIn("<sha256 of the operator's own portrait>", json.dumps(report["examples"]))
        self.assertEqual(report["examples"]["image_edit"]["prompt"], BEAUTY_RETOUCH_PROMPT_EXAMPLE)
        self.assertTrue(calls)
        self.assertTrue(all(method == "GET" for method in calls))


class ReferenceHttpRefusalTest(unittest.TestCase):
    def test_handler_rejects_raw_body_before_any_studio_path(self):
        data = ROOT / "director-data-ref-http"
        if data.exists():
            shutil.rmtree(data)
        app = DirectorApp(ROOT, data, MockTransport(), MockTransport())
        handler = DirectorHandler(app, ROOT)
        status, payload, _media = handler.handle("POST", "/api/director/tasks", b"\xff\xd8\xff", {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "bad_json")
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
