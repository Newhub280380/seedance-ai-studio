"""Mocked prep_photo adapter tests. The fake command writes PNGs. It does not run rembg or Real-ESRGAN."""

import hashlib
import os
import stat
import unittest
from pathlib import Path

from PIL import Image

from director.errors import DirectorRefusal
from director.prep_route import gallery_chrome_crop, onnx_fallback
from director.service import DirectorApp
from tests.test_director_contract import MockTransport, PNG, ROOT

UPLOADS = Path("/home/ubuntu/.cursor/projects/agent-repos-seedance-ai-studio/uploads")


def _write_mock(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import os
import sys
from PIL import Image
args = sys.argv[1:]
source = args[0]
output = args[args.index("-o") + 1]
upscale = args[args.index("--upscale") + 1]
size = int(args[args.index("--size") + 1])
mode = os.environ.get("MOCK_PREP_MODE", "ok")
if mode == "fallback" and upscale == "onnx":
    sys.stderr.write("ВНИМАНИЕ: ONNX модель не найдена (mock), fallback на soft.\\n")
with Image.open(source) as incoming:
    image = incoming.convert("RGBA")
    width, height = image.size
if upscale == "none":
    margin = max(8, min(width, height) // 5)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    inner = image.crop((margin, margin, width - margin, height - margin))
    opaque = Image.new("RGBA", inner.size, (30, 40, 50, 255))
    canvas.paste(opaque, (margin, margin))
    image = canvas
elif mode == "loose_final" and upscale == "onnx":
    image = Image.new("RGBA", (3840, 3840), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (400, 400), (30, 40, 50, 255)), (1720, 1720))
elif mode == "square" and upscale == "onnx":
    image = Image.new("RGBA", (size, size), (10, 10, 10, 255))
    image.putpixel((0, 0), (0, 0, 0, 0))
elif size:
    scale = size / float(max(width, height))
    image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))))
if mode == "rgb":
    image = image.convert("RGB")
image.save(output, format="PNG")
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class PrepRouteTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-prep-test"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.mock = self.data / "mock-prep"
        self.data.mkdir()
        _write_mock(self.mock)
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport(), venice_api_key="mock-venice-key")
        self.app.prep_photo_command = str(self.mock)
        self.app.prep_timeout = 30

    def tearDown(self):
        import shutil
        os.environ.pop("MOCK_PREP_MODE", None)
        shutil.rmtree(self.data, ignore_errors=True)

    def _asset(self):
        return self.app.assets.put(PNG, "image/png")["id"]

    def test_mock_prep_keeps_cutout_and_3840_output(self):
        os.environ["MOCK_PREP_MODE"] = "ok"
        task = self.app.create_task({
            "capability": "image.product_prep",
            "client_request_id": "prep-ok",
            "confirm": True,
            "original_asset_id": self._asset(),
        })
        self.assertEqual(task["status"], "completed", task.get("error"))
        roles = [item["role"] for item in task["results"]]
        self.assertEqual(roles, ["cutout", "tight", "output"])
        self.assertEqual(task["result"]["role"], "output")
        self.assertGreaterEqual(task["result"]["object"]["object_long_side"], int(3840 * 0.98))
        self.assertLessEqual(max(task["result"]["object"]["margin_left"], task["result"]["object"]["margin_right"], task["result"]["object"]["margin_top"], task["result"]["object"]["margin_bottom"]), int(3840 * 0.02))
        output_call = [call for call in task["provider_calls"] if call["role"] == "output"][0]
        self.assertIn("--already-cut", output_call["argv"])
        self.assertTrue(output_call["argv"][1].endswith("tight.png"))
        self.assertGreater(task["prep"]["alpha_bbox"]["native_cutout_size"][0], task["prep"]["alpha_bbox"]["tight_size"][0])
        final = self.app.read_result(task["id"])[0]
        native = self.app.read_result(task["id"], role="cutout")[0]
        explicit_native = self.app.read_result(task["id"], index=0)[0]
        self.assertNotEqual(final, native)
        self.assertEqual(explicit_native, native)
        self.assertTrue(final.startswith(b"\x89PNG"))
        from director.server import DirectorHandler
        handler = DirectorHandler(self.app, ROOT)
        status, payload, _media = handler.handle("GET", "/api/director/tasks/%s/result" % task["id"], b"", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload, final)
        status, payload, _media = handler.handle("GET", "/api/director/tasks/%s/result?role=cutout" % task["id"], b"", {})
        self.assertEqual(payload, native)
        joined = " ".join(" ".join(call["argv"]) for call in task["provider_calls"])
        self.assertIn("--denoise none", joined)
        self.assertIn("--bg transparent", joined)
        self.assertIn("--model u2net", joined)
        self.assertIn("--upscale none", joined)
        self.assertIn("--upscale onnx", joined)
        self.assertNotIn("--alpha-matting", joined)
        self.assertEqual(self.app.venice_transport.calls, [])
        self.assertEqual(task["fidelity"], "cutout_plus_predicted_detail_not_label_identity")
        self.assertEqual(task["prep"]["upscale_backend"], "onnx")
        self.assertFalse(task["prep"]["upscale_fallback"])
        self.assertFalse(task["prep"]["neural_weights_observed"])
        self.assertFalse(task["prep"]["realesrgan_is_beauty_retouch"])
        self.assertFalse(task["prep"]["beauty_retouch"])
        output = self.app.read_result(task["id"])[0]
        self.assertTrue(output.startswith(b"\x89PNG"))

    def test_canvas_3840_with_small_object_is_not_success(self):
        os.environ["MOCK_PREP_MODE"] = "loose_final"
        task = self.app.create_task({
            "capability": "image.product_prep",
            "client_request_id": "prep-loose",
            "confirm": True,
            "original_asset_id": self._asset(),
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "object_below_target")
        self.assertEqual(self.app.read_result(task["id"], role="cutout")[1], "image/png")
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.read_result(task["id"])
        self.assertEqual(caught.exception.code, "final_not_ready")

    def test_missing_alpha_is_not_success(self):
        os.environ["MOCK_PREP_MODE"] = "rgb"
        task = self.app.create_task({
            "capability": "image.product_prep",
            "client_request_id": "prep-rgb",
            "confirm": True,
            "original_asset_id": self._asset(),
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "alpha_missing")

    def test_square_stretch_is_not_success(self):
        os.environ["MOCK_PREP_MODE"] = "square"
        portrait = __import__("director.pngutil", fromlist=["solid_png"]).solid_png(40, 80)
        asset = self.app.assets.put(portrait, "image/png")["id"]
        task = self.app.create_task({
            "capability": "image.product_prep",
            "client_request_id": "prep-square",
            "confirm": True,
            "original_asset_id": asset,
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "aspect_stretched")

    def test_onnx_fallback_keeps_cutout_and_rejects_output(self):
        os.environ["MOCK_PREP_MODE"] = "fallback"
        task = self.app.create_task({
            "capability": "image.product_prep",
            "client_request_id": "prep-fallback",
            "confirm": True,
            "original_asset_id": self._asset(),
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "onnx_fallback")
        self.assertEqual(task["prep"]["upscale_backend"], "soft_fallback")
        self.assertFalse(task["prep"]["neural_upscale_accepted"])
        self.assertEqual(task["results"][0]["role"], "cutout")
        self.assertTrue(onnx_fallback("ВНИМАНИЕ: ONNX модель не найдена (x), fallback на soft."))

    def test_missing_command_does_not_call_venice(self):
        self.app.prep_photo_command = "/no/such/prep_photo"
        task = self.app.create_task({
            "capability": "image.product_prep",
            "client_request_id": "prep-missing",
            "confirm": True,
            "original_asset_id": self._asset(),
        })
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "prep_command_missing")
        self.assertEqual(self.app.venice_transport.calls, [])

    def test_other_model_is_not_substituted(self):
        with self.assertRaises(DirectorRefusal) as caught:
            self.app.create_task({
                "capability": "image.product_prep",
                "client_request_id": "prep-other",
                "confirm": True,
                "model": "birefnet-general",
                "original_asset_id": self._asset(),
            })
        self.assertEqual(caught.exception.code, "model_not_this_lane")

    def test_gallery_crop_matches_supplied_screenshot_when_present(self):
        path = UPLOADS / "zishel-original_ba79.jpg"
        if not path.is_file():
            self.skipTest("supplied screenshot is not in this checkout")
        image = Image.open(path)
        cropped = gallery_chrome_crop(image)
        self.assertIsNotNone(cropped)
        self.assertEqual(cropped.size, (1440, 2558))
        source = image.convert("RGB").crop((0, 281, 1440, 2839))
        self.assertEqual(cropped.getpixel((0, 0)), source.getpixel((0, 0)))
        self.assertEqual(hashlib.sha256(cropped.tobytes()).hexdigest(), hashlib.sha256(source.tobytes()).hexdigest())

    def test_unrelated_image_is_not_cropped(self):
        self.assertIsNone(gallery_chrome_crop(Image.new("RGB", (32, 32), (10, 20, 30))))


if __name__ == "__main__":
    unittest.main()
