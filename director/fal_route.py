"""fal.ai queue route for Wan 2.2 video (text or one photo -> 5 s clip).

Replaces the missing Seedance (Venice) key for clients: one FAL_KEY, one model,
a fixed 5 s clip (81 frames at 16 fps), so a request can never turn into a long,
expensive job. Nothing is sent without confirm=true and a key on the server.
"""

import json

from director.errors import DirectorRefusal, TransportFailure
from director.transport import json_body

QUEUE_BASE = "https://queue.fal.run"
TEXT_ENDPOINT = "fal-ai/wan/v2.2-a14b/text-to-video"
IMAGE_ENDPOINT = "fal-ai/wan/v2.2-a14b/image-to-video"
APP_PREFIX = "fal-ai/wan"  # status/result URLs live under the app id, not the full endpoint
SECONDS = 5
FPS = 16
NUM_FRAMES = SECONDS * FPS + 1
ASPECTS = ["9:16", "16:9", "1:1"]
RESOLUTIONS = ["720p", "580p", "480p"]
PROMPT_LIMIT = 2000

SPEC = {
    "id": "video.fal_wan",
    "title": "Видео 5 с: Wan 2.2 (fal)",
    "provider": "fal",
    "kind": "video_fal",
    "endpoint": "POST " + QUEUE_BASE + "/" + TEXT_ENDPOINT,
    "docs": "https://fal.ai/models/fal-ai/wan/v2.2-a14b/text-to-video/api",
    "models": [],
    "default_model": TEXT_ENDPOINT,
    "resolutions": RESOLUTIONS,
    "aspect_ratios": ASPECTS,
    "durations": [SECONDS],
}


class FalClient:
    def __init__(self, transport, api_key):
        self.transport = transport
        self.api_key = api_key or ""

    def _headers(self, with_body):
        headers = {"Authorization": "Key " + self.api_key}
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    def submit(self, endpoint, payload, timeout=60):
        raw = json.dumps(payload).encode("utf-8")
        try:
            result = self.transport.request("POST", QUEUE_BASE + "/" + endpoint, self._headers(True), raw, timeout)
        except TransportFailure:
            raise
        except TimeoutError as exc:
            raise TransportFailure(str(exc), uncertain=True) from exc
        except Exception as exc:
            raise TransportFailure(str(exc), uncertain=False) from exc
        return result, json_body(result)

    def get(self, url, timeout=30):
        if not url.startswith(QUEUE_BASE + "/"):
            raise DirectorRefusal("fal_url_rejected", "Only queue.fal.run status and result URLs are polled.", 409)
        try:
            result = self.transport.request("GET", url, self._headers(False), None, timeout)
        except TransportFailure:
            raise
        except Exception as exc:
            raise TransportFailure(str(exc), uncertain=False) from exc
        return result, json_body(result)

    def download(self, url, timeout=120):
        try:
            return self.transport.request("GET", url, {}, None, timeout)
        except TransportFailure:
            raise
        except Exception as exc:
            raise TransportFailure(str(exc), uncertain=False) from exc


def prepare(body, prompt, image_asset_id):
    """Validate a client request into fixed Wan 2.2 parameters (always 5 s)."""
    aspect = body.get("aspect_ratio") or "9:16"
    if aspect not in ASPECTS:
        raise DirectorRefusal("aspect_ratio_invalid", "Соотношение %s не поддерживается Wan 2.2. Можно: %s." % (aspect, ", ".join(ASPECTS)))
    resolution = body.get("resolution") or "720p"
    if resolution not in RESOLUTIONS:
        raise DirectorRefusal("resolution_invalid", "Разрешение %s не поддерживается. Можно: %s." % (resolution, ", ".join(RESOLUTIONS)))
    duration = body.get("duration")
    if duration not in (None, "", SECONDS, str(SECONDS), "%ss" % SECONDS):
        raise DirectorRefusal("duration_fixed", "Wan 2.2 здесь делает ролики ровно по %s с (так дешевле всего)." % SECONDS)
    return {
        "model": IMAGE_ENDPOINT if image_asset_id else TEXT_ENDPOINT,
        "prompt": prompt,
        "aspect_ratio": aspect,
        "resolution": resolution,
        "duration_seconds": SECONDS,
        "num_frames": NUM_FRAMES,
        "frames_per_second": FPS,
        "image_asset_id": image_asset_id,
    }


def payload(params, image_data_url=None):
    body = {
        "prompt": params["prompt"],
        "num_frames": params["num_frames"],
        "frames_per_second": params["frames_per_second"],
        "resolution": params["resolution"],
        "aspect_ratio": params["aspect_ratio"],
        "enable_safety_checker": True,
    }
    if image_data_url:
        body["image_url"] = image_data_url
    return body


def status_url(params_or_task, request_id):
    return "%s/%s/requests/%s/status" % (QUEUE_BASE, APP_PREFIX, request_id)


def result_url(request_id):
    return "%s/%s/requests/%s" % (QUEUE_BASE, APP_PREFIX, request_id)


def video_url(body):
    if not isinstance(body, dict):
        return None
    video = body.get("video")
    if isinstance(video, dict):
        return video.get("url")
    return None
