"""MOCKED LTX CLI tests. The fake Python writes an MP4 header. It does not load LTX weights."""

import os
import stat
import unittest
from pathlib import Path

from director.errors import DirectorRefusal
from director.ltx_route import ATTACHMENT_A20, is_mp4
from director.service import DirectorApp
from tests.test_director_contract import MockTransport, ROOT


def _python(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import os, sys
log = os.environ.get("MOCK_LTX_LOG")
if log:
    open(log, "a", encoding="utf-8").write(" ".join(sys.argv) + "\\n")
if "-c" in sys.argv:
    raise SystemExit(0)
if "--output-path" not in sys.argv:
    raise SystemExit(2)
out = sys.argv[sys.argv.index("--output-path") + 1]
if os.environ.get("MOCK_LTX_MODE") == "empty":
    raise SystemExit(0)
open(out, "wb").write(b"\\x00\\x00\\x00\\x18ftypisom" + b"\\x00" * 8)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class LtxRouteTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-ltx-test"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.data.mkdir()
        self.mock = self.data / "mock-python"
        _python(self.mock)
        self.log = self.data / "calls.txt"
        os.environ["MOCK_LTX_LOG"] = str(self.log)
        os.environ["MOCK_LTX_MODE"] = "ok"
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport())
        self.app.ltx_python = str(self.mock)
        self.app.ltx_timeout = 30
        self.model_root = self.data / "models"
        self.model_root.mkdir()
        self.app.ltx_model_root = str(self.model_root)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.data, ignore_errors=True)
        os.environ.pop("MOCK_LTX_LOG", None)
        os.environ.pop("MOCK_LTX_MODE", None)

    def _ready(self):
        self.app._ltx_probe_cached = {
            "ready": True,
            "missing": [],
            "gpu": {"available": True, "reason": "test double, not a machine"},
            "module": {"importable": True, "reason": "mocked"},
            "model_root": str(self.model_root),
            "pin": "test",
            "measured_hosts": {},
            "offload": "",
            "weights_downloaded_by_director": False,
        }

    def _video(self):
        path = self.data / "source.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8)
        return str(path)

    def _lora(self):
        path = self.model_root / "loras" / "control.safetensors"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not-a-weight")
        return str(path)

    def test_this_process_is_not_ready_and_writes_no_mp4(self):
        task = self.app.create_task({
            "capability": "ltx.ic_lora",
            "client_request_id": "ltx-missing",
            "confirm": True,
            "prompt": "Keep the package",
            "video_path": self._video(),
            "lora_path": self._lora(),
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "ltx_not_ready")
        self.assertFalse((self.data / "ltx" / task["id"] / "output.mp4").exists())
        listed = [item for item in self.app.capabilities()["capabilities"] if item["id"] == "ltx.ic_lora"][0]
        self.assertEqual(listed["readiness"]["status"], "ltx_not_ready")
        self.assertTrue(listed["temporal_edit"])
        self.assertIn("video-to-video", listed["summary"])
        self.assertTrue(task["ltx"]["missing"])

    def test_distilled_refuses_source_video(self):
        self._ready()
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "ltx.distilled",
                "client_request_id": "ltx-not-v2v",
                "confirm": True,
                "prompt": "Replace the car",
                "video_path": self._video(),
            })
        self.assertEqual(caught.exception.code, "not_temporal_edit")
        self.assertFalse(self.log.exists())

    def test_ic_lora_mock_mp4_requires_video_and_lora(self):
        self._ready()
        task = self.app.create_task({
            "capability": "ltx.ic_lora",
            "client_request_id": "ltx-v2v",
            "confirm": True,
            "prompt": "Follow the reference motion",
            "video_path": self._video(),
            "lora_path": self._lora(),
            "num_frames": 121,
        })
        self.assertEqual(task["status"], "completed", task.get("error"))
        data, media = self.app.read_result(task["id"])
        self.assertEqual(media, "video/mp4")
        self.assertTrue(is_mp4(self.app.results / task["result"]["file"]))
        self.assertTrue(data[4:8] == b"ftyp")
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("-m ltx_pipelines.ic_lora", text)
        self.assertIn("--video-conditioning", text)
        self.assertIn("--lora", text)
        self.assertNotIn("--enhance-prompt", text)

    def test_empty_process_is_not_completed(self):
        self._ready()
        os.environ["MOCK_LTX_MODE"] = "empty"
        task = self.app.create_task({
            "capability": "ltx.retake",
            "client_request_id": "ltx-empty",
            "confirm": True,
            "prompt": "Regenerate the middle",
            "video_path": self._video(),
            "start_time": 1,
            "end_time": 2,
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "ltx_output_missing")

    def test_commercial_multiuser_stays_off(self):
        self._ready()
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "ltx.distilled",
                "client_request_id": "ltx-saas",
                "confirm": True,
                "prompt": "Sell generations",
                "commercial_multiuser": True,
            })
        self.assertEqual(caught.exception.code, "ltx_commercial_disabled")
        self.assertIn("directly competes", ATTACHMENT_A20)
        self.assertFalse(self.log.exists())

    def test_bad_frame_count_rejected(self):
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "ltx.distilled",
                "client_request_id": "ltx-frames",
                "confirm": True,
                "prompt": "A still camera",
                "num_frames": 10,
            })
        self.assertEqual(caught.exception.code, "frames_rejected")


if __name__ == "__main__":
    unittest.main()
