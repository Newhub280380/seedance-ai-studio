"""MOCKED prep_photo. Not a GPU render and not a beauty retouch.

The fake command writes PNGs. It does not run rembg or Real-ESRGAN.
The public Comfy comparison still is not loaded.
"""

import hashlib
import os
import stat
import unittest
from pathlib import Path

from PIL import Image

from director.errors import DirectorRefusal
from director.pngutil import solid_png
from director.service import DirectorApp
from director.studio_route import BEAUTY_RETOUCH_PROMPT_EXAMPLE
from tests.test_director_contract import MockTransport, ROOT
from tests.test_prep_photo import _write_mock


class SubjectPrepTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-subject-test"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.mock = self.data / "mock-prep"
        self.data.mkdir()
        _write_mock(self.mock)
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport())
        self.app.prep_photo_command = str(self.mock)
        self.app.prep_timeout = 30
        os.environ["MOCK_PREP_MODE"] = "ok"

    def tearDown(self):
        import shutil
        os.environ.pop("MOCK_PREP_MODE", None)
        shutil.rmtree(self.data, ignore_errors=True)

    def _asset(self, blob=None):
        return self.app.assets.put(blob or solid_png(), "image/png")

    def test_person_prep_keeps_png_alpha_jpeg_white_and_original(self):
        before = solid_png()
        original = self._asset(before)
        task = self.app.create_task({
            "capability": "image.subject_prep",
            "client_request_id": "subject-person",
            "confirm": True,
            "subject": "person",
            "original_asset_id": original["id"],
        })
        self.assertEqual(task["status"], "completed", task.get("error"))
        self.assertEqual([item["role"] for item in task["results"]], ["cutout", "tight", "output", "jpeg_white"])
        self.assertEqual(task["result"]["role"], "output")
        self.assertEqual(task["result"]["media_type"], "image/png")
        self.assertFalse(task["params"]["beauty_retouch"])
        self.assertFalse(task["params"]["generative"])
        self.assertFalse(task["params"]["realesrgan_is_beauty_retouch"])
        self.assertFalse(task["prep"]["identity_inferred"])
        self.assertEqual(task["prep"]["upscale_backend"], "onnx")
        self.assertFalse(task["prep"]["neural_weights_observed"])
        self.assertNotIn(BEAUTY_RETOUCH_PROMPT_EXAMPLE, json_of(task))
        self.assertEqual(self.app.assets.get(original["id"])["data"], before)
        self.assertEqual(task["prep"]["original_sha256"], original["id"])
        png, png_type = self.app.read_result(task["id"])
        jpeg, jpeg_type = self.app.read_result(task["id"], role="jpeg_white")
        self.assertEqual(png_type, "image/png")
        self.assertEqual(jpeg_type, "image/jpeg")
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertTrue(jpeg.startswith(b"\xff\xd8\xff"))
        with Image.open(__import__("io").BytesIO(png)) as master, Image.open(__import__("io").BytesIO(jpeg)) as flat:
            self.assertEqual(master.mode, "RGBA")
            self.assertEqual(flat.mode, "RGB")
            self.assertEqual(master.size, flat.size)
            self.assertLess(master.getchannel("A").getextrema()[0], 255)
        self.assertEqual(task["results"][-1]["asset_id"], hashlib.sha256(jpeg).hexdigest())
        self.assertEqual(self.app.venice_transport.calls, [])
        joined = " ".join(" ".join(call["argv"]) for call in task["provider_calls"])
        self.assertEqual(joined.count("--upscale onnx"), 1)
        self.assertNotIn("retouch", joined)
        again = self.app.create_task({
            "capability": "image.subject_prep",
            "client_request_id": "subject-person",
            "confirm": True,
            "subject": "person",
            "original_asset_id": original["id"],
        })
        self.assertEqual(again["id"], task["id"])
        self.assertEqual(len(again["provider_calls"]), len(task["provider_calls"]))

    def test_prompt_and_other_subject_are_refused(self):
        asset = self._asset()["id"]
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "image.subject_prep",
                "client_request_id": "subject-prompt",
                "confirm": True,
                "subject": "person",
                "prompt": BEAUTY_RETOUCH_PROMPT_EXAMPLE,
                "original_asset_id": asset,
            })
        self.assertEqual(caught.exception.code, "prep_is_not_retouch")
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "image.subject_prep",
                "client_request_id": "subject-missing",
                "confirm": True,
                "original_asset_id": asset,
            })
        self.assertEqual(caught.exception.code, "subject_required")
        self.assertEqual(self.app.venice_transport.calls, [])

    def test_soft_fallback_is_not_reported_as_onnx(self):
        os.environ["MOCK_PREP_MODE"] = "fallback"
        task = self.app.create_task({
            "capability": "image.subject_prep",
            "client_request_id": "subject-fallback",
            "confirm": True,
            "subject": "generic",
            "original_asset_id": self._asset()["id"],
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "onnx_fallback")
        self.assertEqual(task["prep"]["upscale_backend"], "soft_fallback")
        self.assertFalse(task["prep"]["neural_upscale_accepted"])
        self.assertNotIn("jpeg_white", [item["role"] for item in task.get("results") or []])

    def test_skipped_upscale_is_not_called_onnx(self):
        script = self.data / "mock-large"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from PIL import Image\n"
            "args = sys.argv[1:]\n"
            "source = args[0]\n"
            "output = args[args.index('-o') + 1]\n"
            "upscale = args[args.index('--upscale') + 1]\n"
            "if upscale != 'none':\n"
            "    raise SystemExit('onnx should not run')\n"
            "with Image.open(source) as incoming:\n"
            "    width, height = incoming.size\n"
            "image = Image.new('RGBA', (width, height), (0, 0, 0, 0))\n"
            "image.paste(Image.new('RGBA', (width - 16, height - 16), (20, 30, 40, 255)), (8, 8))\n"
            "image.save(output, format='PNG')\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.app.prep_photo_command = str(script)
        wide = Image.new("RGBA", (4000, 800), (0, 0, 0, 0))
        wide.paste(Image.new("RGBA", (3984, 784), (20, 30, 40, 255)), (8, 8))
        import io
        raw = io.BytesIO()
        wide.save(raw, format="PNG")
        blob = raw.getvalue()
        self.assertLess(len(blob), 2_000_000)
        task = self.app.create_task({
            "capability": "image.subject_prep",
            "client_request_id": "subject-skip",
            "confirm": True,
            "subject": "object",
            "original_asset_id": self._asset(blob)["id"],
        })
        self.assertEqual(task["status"], "completed", task.get("error"))
        self.assertEqual(task["prep"]["upscale_backend"], "skipped")
        self.assertFalse(task["prep"]["neural_upscale_accepted"])
        self.assertFalse(any(call["role"] == "output" for call in task["provider_calls"]))
        self.assertEqual(task["results"][-1]["role"], "jpeg_white")
        self.assertFalse(task["results"][-1]["second_upscale"])


def json_of(task):
    import json
    return json.dumps(task)


if __name__ == "__main__":
    unittest.main()
