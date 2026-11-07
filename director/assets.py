import hashlib
import os
from pathlib import Path


class AssetStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta = self.root / "meta"
        self.meta.mkdir(exist_ok=True)

    def put(self, data, media_type):
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("empty asset")
        if len(data) > 2_000_000:
            raise ValueError("asset exceeds 2MB in this slice")
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
