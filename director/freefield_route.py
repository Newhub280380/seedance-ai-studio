"""Freefield apps for Режиссёр 1.0: routes under /api/director/freefield/ on top of director/freefield_apps.py.

The catalog, validation and request building are the same file as in Режиссёр 2.0. GET and dry-run are free.
A real run goes to fal only with "confirm": true in the body (same rule as R1 tasks); without it the
server answers 409 with the price, so nothing paid can start by accident.
Uploads (photo, video, audio) live in <data>/freefield_uploads, results in <data>/freefield_results.
"""
import base64
import binascii
import json
import mimetypes
import time
import uuid
from pathlib import Path

from director import freefield_apps as ff
from director.errors import DirectorRefusal, TransportFailure
from director.fal_route import QUEUE_BASE
from director.transport import json_body

UPLOAD_TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/ogg": ".ogg", "audio/flac": ".flac",
}
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".mp4": "video/mp4",
         ".mov": "video/quicktime", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
         ".ogg": "audio/ogg", ".flac": "audio/flac"}


class _FalQueue:
    """fal queue client for freefield_apps.start_run, built on R1's FalClient (one transport, no retries on submit)."""

    def __init__(self, fal, sleep=time.sleep, interval=4.0, timeout=1800.0):
        self.fal, self.sleep, self.interval, self.timeout = fal, sleep, interval, timeout

    def call(self, model, body, cancelled=lambda: False):
        result, sub = self.fal.submit(model, body)
        if result.status >= 400 or not isinstance(sub, dict):
            raise ff.AppError("fal %s: HTTP %s %s" % (model, result.status, (result.body or b"")[:300].decode("utf-8", "replace")))
        status_url, response_url = sub.get("status_url"), sub.get("response_url")
        if not (status_url and response_url):
            raise ff.AppError("fal %s: неожиданный ответ очереди" % model)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if cancelled():
                raise ff.AppError("Отменено")
            self.sleep(self.interval)
            _, state = self.fal.get(status_url)
            status = (state or {}).get("status")
            if status == "COMPLETED":
                res, payload = self.fal.get(response_url)
                if res.status >= 400 or not isinstance(payload, dict):
                    raise ff.AppError("fal %s: результат не получен (HTTP %s)" % (model, res.status))
                return payload
            if status in ("FAILED", "ERROR", "CANCELLED"):
                raise ff.AppError("fal %s: %s" % (model, status))
        raise ff.AppError("fal %s: истекло время ожидания" % model)


class FreefieldR1:
    def __init__(self, app, sleep=time.sleep):
        self.app = app
        self.uploads = Path(app.data_dir) / "freefield_uploads"
        self.results = Path(app.data_dir) / "freefield_results"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)
        self.queue = _FalQueue(app.fal, sleep=sleep)

    # -------- files
    def resolve(self, name):
        base = self.uploads.resolve()
        path = (base / Path(str(name)).name).resolve()
        if path.parent != base or not path.is_file():
            raise ff.AppError("Файл %s не найден среди загрузок." % Path(str(name)).name)
        return path

    def upload(self, payload):
        media = str(payload.get("media_type") or "").lower()
        if media not in UPLOAD_TYPES:
            raise DirectorRefusal("unsupported_media_type", "Можно фото (png/jpg/webp), видео (mp4/mov/webm) или звук (mp3/wav/m4a/ogg/flac).", 400)
        raw = payload.get("content_base64")
        if not isinstance(raw, str) or not raw or raw.startswith("data:"):
            raise DirectorRefusal("bad_base64", "content_base64 — обычный base64 файла, не data URL.", 400)
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DirectorRefusal("bad_base64", "content_base64 не похож на base64.", 400) from exc
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise DirectorRefusal("asset_too_large", "Файл пустой или больше 40 МБ.", 400)
        name = uuid.uuid4().hex[:12] + UPLOAD_TYPES[media]
        (self.uploads / name).write_bytes(data)
        return {"filename": name, "bytes": len(data), "media_type": media}

    @staticmethod
    def data_uri(path):
        path = Path(path)
        media = MEDIA.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return "data:%s;base64,%s" % (media, base64.b64encode(path.read_bytes()).decode("ascii"))

    def download(self, url, out):
        result = self.app.fal.download(url)
        if result.status >= 400 or not result.body:
            raise ff.AppError("Не удалось скачать результат (HTTP %s)" % result.status)
        Path(out).write_bytes(result.body)
        return out

    def read_file(self, name):
        base = self.results.resolve()
        path = (base / Path(str(name)).name).resolve()
        if path.parent != base or not path.is_file():
            raise DirectorRefusal("not_found", "Нет такого файла", 404)
        return path.read_bytes(), MEDIA.get(path.suffix.lower(), "application/octet-stream")

    # -------- api
    def catalog(self):
        return dict(ff.catalog(), fal_ready=bool(self.app.fal_api_key), confirm_required=True)

    def dry_run(self, app_id, body):
        return ff.dry_run(app_id, _inputs(body), self.resolve)

    def run(self, app_id, body, background=True):
        app = ff.APPS_BY_ID.get(app_id)
        if app is None:
            raise ff.AppError("Нет такого приложения: %s" % app_id)
        if body.get("confirm") is not True:
            plan = ff.dry_run(app_id, _inputs(body), self.resolve)
            raise DirectorRefusal("confirm_required", "Это платный запуск в fal (%s). Повторите с \"confirm\": true." % plan["price"], 409)
        if not self.app.fal_api_key:
            raise DirectorRefusal("fal_key_missing", "Нет ключа fal в окружении сервера (FAL_KEY).", 409)
        return ff.start_run(app_id, _inputs(body), resolve_file=self.resolve, file_to_url=self.data_uri,
                            client=self.queue, out_dir=self.results, download=self.download,
                            url_for=lambda name: "/api/director/freefield/files/" + name, background=background)


def _inputs(body):
    body = body if isinstance(body, dict) else {}
    return body.get("inputs") if isinstance(body.get("inputs"), dict) else {k: v for k, v in body.items() if k != "confirm"}


def handle(app, method, route, body):
    """Returns (status, payload, media) for /api/director/freefield/... or None if the route is not ours."""
    if not route.startswith("/api/director/freefield"):
        return None
    ffr = getattr(app, "_freefield", None)
    if ffr is None:
        ffr = app._freefield = FreefieldR1(app)
    parts = route.strip("/").split("/")[3:]  # after api/director/freefield
    try:
        if method == "GET" and parts == ["apps"]:
            return 200, ffr.catalog(), "application/json"
        if method == "POST" and parts == ["uploads"]:
            return 200, {"ok": True, **ffr.upload(_json(body))}, "application/json"
        if method == "POST" and len(parts) == 3 and parts[0] == "apps" and parts[2] == "dry-run":
            return 200, ffr.dry_run(parts[1], _json(body)), "application/json"
        if method == "POST" and len(parts) == 3 and parts[0] == "apps" and parts[2] == "run":
            return 200, ffr.run(parts[1], _json(body)), "application/json"
        if method == "GET" and len(parts) == 2 and parts[0] == "runs":
            state = ff.get_run(parts[1])
            if not state:
                raise DirectorRefusal("not_found", "Неизвестный запуск", 404)
            return 200, state, "application/json"
        if method == "GET" and len(parts) == 2 and parts[0] == "files":
            data, media = ffr.read_file(parts[1])
            return 200, data, media
        return 404, {"ok": False, "error": {"code": "not_found", "message": "No such freefield route"}}, "application/json"
    except ff.AppError as exc:
        return 400, {"ok": False, "error": {"code": "freefield_invalid", "message": str(exc)}}, "application/json"
    except TransportFailure as exc:
        return 502, {"ok": False, "error": {"code": "transport", "message": str(exc)}}, "application/json"


def _json(body):
    if not body:
        return {}
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DirectorRefusal("bad_json", "Request body is not JSON", 400) from exc
    if not isinstance(value, dict):
        raise DirectorRefusal("bad_json", "JSON object required", 400)
    return value
