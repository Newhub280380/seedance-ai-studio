"""Optional adapter for Imagination Studio by keys+Wasserman.

The Studio process is upstream. This module only reads its localhost API and
submits a job when the Studio itself says the renderer is installed.
It does not download weights and it does not start the Studio's own film director.

Image edit and reference video use the routes already in tag v4.3.1
(commit 3926471d59bfe8a51a295b7bfdef6cda0b09e1de): POST /api/upload,
POST /api/jobs with kind edit or video, GET /api/jobs/{id}, GET /files/{path},
GET /api/edit/state. No endpoint is invented here. No source patch is required.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

PIN = "3926471d59bfe8a51a295b7bfdef6cda0b09e1de"
VERSION = "4.3.1"
REPO = "https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman"
DEFAULT_URL = "http://127.0.0.1:7870"
MAX_POLLS = 240
CLASSIC_SECONDS = (5, 8, 10, 15)
# picture_edit.py / server.py IMAGE_MODELS at the pin. H3 is not in this list.
IMAGE_EDIT_MODELS = ("qwen", "qwen-full", "flux3k-fast", "flux3k-full", "moss-image")

# Operator text for a cosmetic edit. It is not a default, not a prep step, and not a medical protocol.
BEAUTY_RETOUCH_PROMPT_EXAMPLE = (
    "Edit image 1 only. Subtle natural rejuvenation. "
    "Soften under-eye circles and wrinkles that are already visible. "
    "Whiten only teeth that are already visible. If the mouth is closed, do not add teeth. "
    "Keep the same person, expression, anatomy and skin texture. "
    "Do not invent missing anatomy. This is a cosmetic picture edit, not a medical result."
)

PRODUCTION_IDENTITY = {
    "bundled_portrait": None,
    "default_reference_asset_id": None,
    "identity_inferred_from_uploads": False,
    "comfy_public_sample_is_production_reference": False,
    "portrait_status": "not_in_this_repository",
    "note": (
        "The production reference is the operator's own portrait. That file is not in this repository and is not inferred from other uploads. "
        "The public Comfy standing-person comparison is not a default asset, not an example payload, and not the identity reference."
    ),
}

# Confirmed in app/server.py, app/picture_edit.py, app/swap.py, app/revise.py and scripts/edit.sh of tag v4.3.1.
FAMILIES = (
    {
        "id": "studio.image_edit",
        "title": "Studio: правка изображения по файлу-референсу",
        "endpoint": "POST /api/jobs",
        "kind_name": "edit",
        "submits": True,
        "needs_h3": False,
        "summary": (
            "POST /api/upload, then POST /api/jobs kind=edit. The picture file is params.image. "
            "scripts/edit.sh feeds that file to Qwen-Image-2.1 or FLUX3-K as image 1. "
            "An explicit prompt is required. No prompt is filled in, including the cosmetic example. "
            "Prep and Real-ESRGAN do not perform this edit. The edit result is a new file. "
            "H3 and Ref2VA are not required. This is generative and does not lock identity."
        ),
    },
    {
        "id": "studio.reference_video",
        "title": "Studio: видео по референс-кадрам Ref2VA",
        "endpoint": "POST /api/jobs",
        "kind_name": "video",
        "submits": True,
        "needs_h3": True,
        "summary": (
            "Optional. POST /api/upload, then POST /api/jobs kind=video with params.references. "
            "scripts/video.sh reads REF_IMAGES. The prompt must name <Picture 1>. "
            "A reference is not a first frame. This needs MiniMax H3 Ref2VA and is not the image-edit route."
        ),
    },
    {
        "id": "studio.swap",
        "title": "Studio: замена персонажа в ролике",
        "endpoint": "POST /api/jobs",
        "kind_name": "swap",
        "submits": True,
        "needs_h3": True,
        "summary": (
            "Optional Character Swap. It repaints the largest person in one continuous shot with Ref2VA. "
            "It is not a transparent background cutout, not the image-edit route, and it does not guarantee the same face."
        ),
    },
    {
        "id": "studio.remove",
        "title": "Studio: убрать человека из ролика",
        "endpoint": "POST /api/jobs",
        "kind_name": "remove",
        "submits": True,
        "needs_h3": False,
        "summary": (
            "Official Remove a person. The guide and the job are video inpainting, not a transparent PNG. "
            "Pixels outside the mask, including a face or clothes, can change. The CPU cutout stays image.subject_prep or image.product_prep."
        ),
    },
    {
        "id": "studio.revise",
        "title": "Studio: restyle, extend или fix",
        "endpoint": "POST /api/revise",
        "kind_name": "revise",
        "submits": False,
        "needs_h3": True,
        "summary": (
            "POST /api/revise is real. Kinds are restyle, extend and fix, and each one needs a clip already in the Studio library. "
            "Director does not submit one while Ref2VA is absent."
        ),
    },
    {
        "id": "studio.slate_direct",
        "title": "Studio: свой режиссёр фильма",
        "endpoint": "POST /api/slate/direct",
        "kind_name": "slate_direct",
        "submits": False,
        "needs_h3": False,
        "summary": (
            "POST /api/slate/direct and GET /api/slate/direct/{project} exist in the Studio. "
            "Calling the first one starts the Studio's own director thread. This Director does not call it."
        ),
    },
)

CLINICAL_KEYS = ("clinical_protocol", "injection_dose", "dose_ml", "before_after", "carousel")


def family(capability_id: str) -> dict | None:
    for item in FAMILIES:
        if item["id"] == capability_id:
            return item
    return None


def request_json(method: str, url: str, body: dict | None = None, timeout: int = 8) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    return _read(method, url, data, headers, timeout, json_response=True)


def encode_multipart(filename: str, data: bytes, content_type: str) -> tuple[str, bytes]:
    safe = os.path.basename(filename)
    if safe != filename or not _safe_filename(safe):
        raise ValueError("upload filename")
    if content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError("upload type")
    boundary = "----director" + hashlib.sha256(data).hexdigest()
    raw = b"".join([
        b"--" + boundary.encode("ascii") + b"\r\n",
        ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % safe).encode("ascii"),
        ("Content-Type: %s\r\n\r\n" % content_type).encode("ascii"),
        data,
        b"\r\n--" + boundary.encode("ascii") + b"--\r\n",
    ])
    return boundary, raw


def exchange(method: str, url: str, body=None, timeout: int = 30) -> tuple[int, object]:
    if isinstance(body, tuple) and body and body[0] == "file":
        _, filename, data, content_type = body
        boundary, raw = encode_multipart(filename, data, content_type)
        headers = {
            "Accept": "application/json",
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        }
        return _read(method, url, raw, headers, timeout, json_response=True)
    if method == "GET" and "/files/" in url:
        return _read(method, url, None, {"Accept": "*/*"}, timeout, json_response=False)
    return request_json(method, url, body, timeout)


def file_url(base: str, relative: str) -> str:
    return base.rstrip("/") + "/files/" + urllib.parse.quote(studio_relative(relative), safe="/")


def studio_relative(path: object) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("empty studio path")
    text = path.strip()
    if text.startswith("/") or "\\" in text or ".." in text.split("/"):
        raise ValueError("unsafe studio path")
    return text


def suffix_for(media: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4"}[media]


def discover(base: str, transport=None, timeout: int = 8) -> dict:
    """Read the live Studio. No job is submitted here."""

    call = transport or request_json
    root = base.rstrip("/")
    report = {"base": root, "reachable": False, "error": "", "gets": []}

    def grab(path: str):
        report["gets"].append(path)
        status, payload = call("GET", root + path, None, timeout)
        if status >= 400:
            raise urllib.error.URLError("HTTP %s %s" % (status, path))
        return payload

    try:
        report["health"] = grab("/api/updates/health")
        report["reachable"] = True
        report["updates"] = grab("/api/updates")
        report["swap"] = grab("/api/swap")
        report["actions"] = grab("/api/actions")
        report["revise"] = grab("/api/revise")
        report["brains"] = grab("/api/brains")
        state = grab("/api/state")
        report["state"] = {
            "version": state.get("version"),
            "platform_label": state.get("platform_label"),
            "gpu_index": state.get("gpu_index"),
            "ref2va": state.get("ref2va"),
            "ffmpeg": state.get("ffmpeg"),
            "engine": state.get("engine"),
            "image_models": state.get("image_models") or [],
            "reference_limit": state.get("reference_limit"),
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
        report["error"] = str(exc)
    if report["reachable"]:
        report["gets"].append("/api/edit/state")
        try:
            status, payload = call("GET", root + "/api/edit/state", None, timeout)
            if status >= 400 or not isinstance(payload, dict):
                report["edit"] = {"ready": False, "why": "HTTP %s /api/edit/state" % status}
            else:
                report["edit"] = payload
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
            report["edit"] = {"ready": False, "why": str(exc)}
    return report


def brief(report: dict) -> dict:
    actions = report.get("actions") if isinstance(report.get("actions"), dict) else {}
    remove = actions.get("remove") if isinstance(actions.get("remove"), dict) else {}
    swap = report.get("swap") if isinstance(report.get("swap"), dict) else {}
    brains = report.get("brains") if isinstance(report.get("brains"), dict) else {}
    revise = report.get("revise") if isinstance(report.get("revise"), dict) else {}
    state = report.get("state") if isinstance(report.get("state"), dict) else {}
    updates = report.get("updates") if isinstance(report.get("updates"), dict) else {}
    edit = report.get("edit") if isinstance(report.get("edit"), dict) else {}
    models = [item.get("id") for item in edit.get("models") or [] if isinstance(item, dict) and item.get("id")]
    return {
        "reachable": bool(report.get("reachable")),
        "error": report.get("error") or "",
        "version": state.get("version"),
        "platform_label": state.get("platform_label"),
        "gpu_index": state.get("gpu_index"),
        "ref2va": state.get("ref2va"),
        "engine": state.get("engine"),
        "brain_default": brains.get("default"),
        "updates_automatic": updates.get("automatic"),
        "updates_managed": updates.get("managed"),
        "swap_ready": bool(swap.get("ready")),
        "swap_why": swap.get("why") or "",
        "remove_ready": bool(remove.get("ready")),
        "remove_why": remove.get("why") or "",
        "edit_ready": bool(edit.get("ready")),
        "edit_why": edit.get("why") or "",
        "edit_models": models,
        "image_models": state.get("image_models") or [],
        "reference_limit": state.get("reference_limit"),
        "revise_kinds": [item.get("kind") for item in revise.get("kinds") or [] if isinstance(item, dict)],
        "weights_downloaded_by_director": False,
        "gpu_tested_by_director": False,
        "pin": PIN,
        "expected_version": VERSION,
    }


def readiness(capability_id: str, report: dict) -> str:
    info = brief(report)
    if not info["reachable"]:
        return "studio_unavailable"
    if capability_id == "studio.slate_direct":
        return "studio_direct_not_submitted"
    if capability_id == "studio.image_edit":
        return "ready_to_confirm" if info["edit_ready"] else "studio_renderer_unavailable"
    if capability_id == "studio.reference_video":
        return "ready_to_confirm" if info["ref2va"] else "studio_renderer_unavailable"
    if capability_id == "studio.swap":
        return "ready_to_confirm" if info["swap_ready"] else "studio_renderer_unavailable"
    if capability_id == "studio.remove":
        return "ready_to_confirm" if info["remove_ready"] else "studio_renderer_unavailable"
    if capability_id == "studio.revise":
        if not info["ref2va"]:
            return "studio_renderer_unavailable"
        return "studio_clip_required"
    return "studio_unavailable"


def _read(method, url, data, headers, timeout, json_response: bool) -> tuple[int, object]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not json_response:
                return response.status, raw
            return response.status, json.loads(raw.decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if not json_response:
            return exc.code, raw
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except ValueError:
            parsed = {"error": raw.decode("utf-8", errors="replace")[:300]}
        return exc.code, parsed


def _safe_filename(name: str) -> bool:
    if len(name) < 8 or not name.startswith("reference-"):
        return False
    stem, dot, ext = name.rpartition(".")
    return bool(dot) and ext in ("png", "jpg", "webp") and stem[len("reference-"):].isdigit()
