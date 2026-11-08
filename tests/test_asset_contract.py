"""Asset POST contract. No network and no portrait.

A raw image body is bad_json. The id is the sha256 of the decoded bytes.
"""

import base64
import hashlib
import json
import unittest

from director.assets import MAX_BYTES, POST_SCHEMA
from director.pngutil import solid_png
from director.server import DirectorHandler
from director.service import DirectorApp
from tests.test_director_contract import MockTransport, ROOT


class AssetContractTest(unittest.TestCase):
    def setUp(self):
        self.data = ROOT / "director-data-asset-test"
        if self.data.exists():
            import shutil
            shutil.rmtree(self.data)
        self.app = DirectorApp(ROOT, self.data, MockTransport(), MockTransport())
        self.handler = DirectorHandler(self.app, ROOT)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.data, ignore_errors=True)

    def test_schema_is_published_and_raw_jpeg_is_bad_json(self):
        payload = self.app.capabilities()
        self.assertEqual(payload["assets"]["path"], "/api/director/assets")
        self.assertEqual(payload["assets"]["limits"]["max_decoded_bytes"], MAX_BYTES)
        self.assertIsNone(payload["assets"]["limits"]["rate_limit"])
        self.assertEqual(payload["assets"], POST_SCHEMA)
        self.assertIsNone(payload["production_identity"]["default_reference_asset_id"])
        self.assertFalse(payload["production_identity"]["identity_inferred_from_uploads"])
        self.assertFalse(payload["production_identity"]["comfy_public_sample_is_production_reference"])
        self.assertFalse(payload["beauty_retouch"]["applied_by_default"])
        self.assertFalse(payload["beauty_retouch"]["performed_by_realesrgan"])
        status, body, _media = self.handler.handle("POST", "/api/director/assets", b"\xff\xd8\xff\xd9", {})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_json")

    def test_json_upload_id_is_sha256_and_repeats(self):
        blob = solid_png(8, 6, (1, 2, 3))
        encoded = base64.b64encode(blob).decode("ascii")
        raw = json.dumps({"media_type": "image/png", "content_base64": encoded}).encode("utf-8")
        status, body, _media = self.handler.handle("POST", "/api/director/assets", raw, {})
        self.assertEqual(status, 200)
        digest = hashlib.sha256(blob).hexdigest()
        self.assertEqual(body["asset"]["id"], digest)
        self.assertEqual(body["asset"]["sha256"], digest)
        self.assertEqual(body["asset"]["bytes"], len(blob))
        self.assertFalse(body["asset"]["identity_inferred"])
        again, second, _media = self.handler.handle("POST", "/api/director/assets", raw, {})
        self.assertEqual(again, 200)
        self.assertEqual(second["asset"]["id"], digest)
        self.assertEqual(self.app.assets.get(digest)["data"], blob)

    def test_empty_oversize_mismatch_and_bad_base64(self):
        status, body, _media = self.handler.handle(
            "POST", "/api/director/assets", json.dumps({"media_type": "image/png", "content_base64": ""}).encode(), {}
        )
        self.assertEqual(body["error"]["code"], "asset_empty")
        status, body, _media = self.handler.handle(
            "POST", "/api/director/assets", json.dumps({"content_base64": "%%%"}).encode(), {}
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_base64")
        jpeg = b"\xff\xd8\xff\xd9"
        status, body, _media = self.handler.handle(
            "POST",
            "/api/director/assets",
            json.dumps({"media_type": "image/png", "content_base64": base64.b64encode(jpeg).decode()}).encode(),
            {},
        )
        self.assertEqual(body["error"]["code"], "media_type_mismatch")
        status, body, _media = self.handler.handle(
            "POST",
            "/api/director/assets",
            json.dumps({"media_type": "image/png", "content_base64": base64.b64encode(b"not-an-image").decode()}).encode(),
            {},
        )
        self.assertEqual(body["error"]["code"], "bad_reference")
        huge = b"\x89PNG\r\n\x1a\n" + b"x" * MAX_BYTES
        status, body, _media = self.handler.handle(
            "POST",
            "/api/director/assets",
            json.dumps({"media_type": "image/png", "content_base64": base64.b64encode(huge).decode()}).encode(),
            {},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "asset_too_large")


if __name__ == "__main__":
    unittest.main()
