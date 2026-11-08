import hashlib
import os
from pathlib import Path

# Decoded file bytes. The HTTP body is JSON, not the image itself.
MAX_BYTES = 2_000_000

POST_SCHEMA = {
    "method": "POST",
    "path": "/api/director/assets",
    "content_type": "application/json; charset=utf-8",
    "raw_body": {
        "image/jpeg": "bad_json",
        "image/png": "bad_json",
        "note": "A raw image body is not this route. The bytes go in content_base64.",
    },
    "json": {
        "content_base64": "required string. Standard base64 of the file bytes. Not a data URL. No whitespace.",
        "media_type": "image/png, image/jpeg or image/webp. image/jpg is stored as image/jpeg. Omitted means image/png. The decoded magic bytes must match.",
    },
    "limits": {
        "min_decoded_bytes": 1,
        "max_decoded_bytes": MAX_BYTES,
        "rate_limit": None,
        "idempotency": "The id is the sha256 of the exact decoded bytes. The same bytes return the same id and do not create a second file.",
    },
    "response_asset": ["id", "sha256", "bytes", "media_type"],
}


class AssetStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta = self.root / "meta"
        self.meta.mkdir(exist_ok=True)

    def put(self, data, media_type):
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("empty asset")
        if len(data) > MAX_BYTES:
            raise ValueError("asset exceeds %s bytes" % MAX_BYTES)
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / digest
        if not target.exists():
            temporary = self.root / (digest + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        record = {"id": digest, "sha256": digest, "bytes": len(data), "media_type": media_type}
        (self.meta / digest).write_text(
            __import__("json").dumps(record), encoding="utf-8"
        )
        stored = target.read_bytes()
        if hashlib.sha256(stored).hexdigest() != digest:
            raise RuntimeError("asset hash changed after write")
        return record

    def get(self, asset_id):
        path = self.root / asset_id
        meta_path = self.meta / asset_id
        if not path.is_file() or not meta_path.is_file():
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset_id:
            raise RuntimeError("stored original no longer matches its id")
        meta = __import__("json").loads(meta_path.read_text(encoding="utf-8"))
        meta["data"] = data
        return meta
