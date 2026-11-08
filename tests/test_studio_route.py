"""Studio adapter tests.

The live class talks to a process only when one is already on 127.0.0.1:7870.
The other class uses a canned report. A canned ready flag is not a render.
"""

import unittest

from director.errors import DirectorRefusal
from director.service import DirectorApp
from director.studio_route import PIN, VERSION
from tests.test_director_contract import MockTransport, ROOT


def _report(swap_ready=False, remove_ready=False, ref2va=False, reachable=True):
    return {
        "reachable": reachable,
        "error": "" if reachable else "connection refused",
        "health": {"session": ""},
        "updates": {"automatic": False, "managed": False, "installed_version": VERSION},
        "swap": {"ready": swap_ready, "why": "" if swap_ready else "MiniMax H3 Ref2VA is not installed."},
        "actions": {"remove": {"ready": remove_ready, "why": "" if remove_ready else "workflows are not installed."}},
        "revise": {"kinds": [{"kind": "restyle"}, {"kind": "extend"}, {"kind": "fix"}], "chains": []},
        "brains": {"default": "local"},
        "state": {"version": VERSION, "platform_label": "test", "gpu_index": None, "ref2va": ref2va, "engine": "none"},
    }


class StudioGuardTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-studio-test"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.data.mkdir()
        self.calls = []
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport())
        self.app.studio_url = "http://studio.test"
        self.app.studio_transport = self._transport
        self.app._studio_probe_cached = _report()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.data, ignore_errors=True)

    def _transport(self, method, url, body, timeout):
        self.calls.append((method, url, body))
        return 200, {"id": "job-1", "status": "queued"}

    def test_missing_renderer_does_not_post_or_complete(self):
        task = self.app.create_task({
            "capability": "studio.swap",
            "client_request_id": "studio-swap-missing",
            "confirm": True,
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "studio_renderer_unavailable")
        self.assertNotIn("result", task)
        self.assertEqual(self.calls, [])
        listed = [item for item in self.app.capabilities()["capabilities"] if item["id"] == "studio.remove"][0]
        self.assertEqual(listed["readiness"]["status"], "studio_renderer_unavailable")
        self.assertFalse(listed["studio"]["remove_ready"])
        self.assertIn("image.product_prep", [item["id"] for item in self.app.capabilities()["capabilities"]])

    def test_slate_direct_is_not_called(self):
        task = self.app.create_task({
            "capability": "studio.slate_direct",
            "client_request_id": "studio-direct",
            "confirm": True,
        })
        self.assertEqual(task["error"]["code"], "studio_direct_not_submitted")
        self.assertEqual(self.calls, [])

    def test_ready_acceptance_is_not_a_finished_picture(self):
        self.app._studio_probe_cached = _report(swap_ready=True, ref2va=True)
        task = self.app.create_task({
            "capability": "studio.swap",
            "client_request_id": "studio-swap-ready",
            "confirm": True,
            "studio_params": {"video": "renders/a.mp4", "image": "uploads/a.png"},
        })
        self.assertEqual(task["status"], "upstream_accepted")
        self.assertNotIn("result", task)
        self.assertEqual(self.calls[0][0], "POST")
        self.assertIn("/api/jobs", self.calls[0][1])
        self.assertEqual(self.calls[0][2]["kind"], "swap")

    def test_private_face_flag_is_refused(self):
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "studio.remove",
                "client_request_id": "studio-face",
                "confirm": True,
                "upload_private_face": True,
            })
        self.assertEqual(caught.exception.code, "private_face_refused")

    def test_down_server_is_unavailable(self):
        self.app._studio_probe_cached = _report(reachable=False)
        task = self.app.create_task({
            "capability": "studio.remove",
            "client_request_id": "studio-down",
            "confirm": True,
        })
        self.assertEqual(task["error"]["code"], "studio_unavailable")
        self.assertEqual(self.calls, [])


class StudioLiveTest(unittest.TestCase):
    """REAL process on this machine. Skipped when nothing is listening. Not a face or background render."""

    def test_live_health_and_director_preflight(self):
        from director.studio_route import discover, request_json
        try:
            status, _health = request_json("GET", "http://127.0.0.1:7870/api/updates/health", timeout=3)
        except Exception as exc:  # noqa: BLE001
            self.skipTest("no live Studio: %s" % exc)
        self.assertEqual(status, 200)
        report = discover("http://127.0.0.1:7870", timeout=8)
        self.assertTrue(report["reachable"])
        self.assertEqual(report["state"]["version"], VERSION)
        self.assertEqual(report["brains"]["default"], "local")
        self.assertFalse(report["updates"]["automatic"])
        self.assertFalse(report["updates"]["managed"])
        self.assertFalse(report["swap"]["ready"])
        self.assertFalse(report["actions"]["remove"]["ready"])
        self.assertIsNone(report["state"]["gpu_index"])
        self.assertFalse(report["state"]["ref2va"])
        data = ROOT / "director-data-studio-live"
        if data.exists():
            import shutil
            shutil.rmtree(data)
        app = DirectorApp(ROOT, data, MockTransport(), MockTransport())
        app.studio_url = "http://127.0.0.1:7870"
        task = app.create_task({
            "capability": "studio.swap",
            "client_request_id": "studio-live-preflight",
            "confirm": True,
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "studio_renderer_unavailable")
        self.assertIn("Ref2VA", task["error"]["message"])
        self.assertNotIn("result", task)
        code, jobs = request_json("GET", "http://127.0.0.1:7870/api/jobs", timeout=5)
        self.assertEqual(code, 200)
        self.assertEqual(jobs, [])
        self.assertEqual(PIN, "3926471d59bfe8a51a295b7bfdef6cda0b09e1de")
        import shutil
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
