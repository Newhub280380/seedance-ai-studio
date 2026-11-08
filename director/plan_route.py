"""Text-only shot plan from the operator's local OpenAI-compatible brain.

This does not start Studio, does not call /api/slate/direct, and does not invent
a plan when the model reply is missing or invalid.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from director.errors import DirectorRefusal

ROLES = ("cast", "place", "wardrobe", "style_motion")
PICTURE_ROLES = ("cast", "place", "wardrobe")
CONSTRAINTS = {
    "age_preserved": True,
    "identity_preserved": True,
    "fake_encounter": False,
    "endorsement": False,
    "adult_body_face_paste": False,
}
SYSTEM = (
    "Reply with one JSON object and no other text. "
    "You are writing a shot plan only. You cannot render, edit, or save a film. "
    "render_started must be false. "
    "duration_seconds is 120 and aspect_ratio is 9:16. "
    "Use only the asset ids you were given. Do not invent a photograph or a person. "
    "Roles cast, place and wardrobe may be pictured. "
    "Role style_motion is style and motion only: not someone who appears, not a meeting, "
    "not an endorsement, and not a body that receives another person's face. "
    "Keep the ages and identities of the cast. "
    "Shots start at 0, are contiguous, and their duration_seconds are positive integers that sum to 120. "
    "Each shot needs a summary, an image_prompt and a video_prompt. "
    "constraints must be "
    '{"age_preserved": true, "identity_preserved": true, "fake_encounter": false, '
    '"endorsement": false, "adult_body_face_paste": false}. '
    "Echo references exactly as given."
)


def brain_base(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise DirectorRefusal(
            "brain_not_configured",
            "Set DIRECTOR_BRAIN_URL and DIRECTOR_BRAIN_MODEL. No plan was invented.",
        )
    parts = urllib.parse.urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or parts.username or parts.password or parts.query or parts.fragment:
        raise DirectorRefusal(
            "brain_endpoint_rejected",
            "DIRECTOR_BRAIN_URL must be an http(s) loopback /v1 base with no user, query or fragment.",
        )
    if host not in ("127.0.0.1", "localhost", "::1") or not parts.port:
        raise DirectorRefusal(
            "brain_endpoint_rejected",
            "DIRECTOR_BRAIN_URL must be a loopback host with an explicit port. A public brain was not called.",
        )
    if parts.path.rstrip("/") != "/v1":
        raise DirectorRefusal("brain_endpoint_rejected", "DIRECTOR_BRAIN_URL must end with /v1.")
    netloc = "[::1]" if host == "::1" else host
    return "%s://%s:%s/v1" % (parts.scheme, netloc, parts.port)


def messages(params: dict) -> list[dict]:
    payload = {
        "brief": params["brief"],
        "duration_seconds": 120,
        "aspect_ratio": "9:16",
        "references": params["references"],
        "image_bytes_included": False,
        "render": False,
    }
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def chat(base: str, model: str, messages_value: list[dict], timeout: int, transport=None) -> tuple[int, object]:
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "temperature": 0, "messages": messages_value}
    if transport is not None:
        return transport("POST", url, body, timeout)
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except ValueError:
            parsed = {"error": raw.decode("utf-8", errors="replace")[:300]}
        return exc.code, parsed
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise DirectorRefusal("brain_unavailable", "The local brain did not answer: %s" % exc) from exc


def parse_json(text: str) -> dict:
    cleaned = text.replace("```json", "").replace("```", "")
    parts = [cleaned.rsplit("</think>", 1)[-1]] if "</think>" in cleaned else []
    parts.append(cleaned)
    for part in parts:
        found = _first_object(part.strip())
        if isinstance(found, dict):
            return found
    raise DirectorRefusal("plan_invalid", "The brain reply had no JSON object. No substitute plan was written.")


def validate_plan(plan: dict, params: dict) -> dict:
    if not isinstance(plan, dict):
        raise DirectorRefusal("plan_invalid", "The brain JSON is not an object. No substitute plan was written.")
    if plan.get("render_started") is not False:
        raise DirectorRefusal("plan_invalid", "render_started must be false. Planning does not start a render.")
    if plan.get("duration_seconds") != 120 or plan.get("aspect_ratio") != "9:16":
        raise DirectorRefusal("plan_invalid", "The plan must say duration_seconds 120 and aspect_ratio 9:16.")
    if plan.get("constraints") != CONSTRAINTS:
        raise DirectorRefusal("plan_invalid", "The plan constraints were not the required identity rules. They were not rewritten.")
    _text(plan.get("title"), "title", 1, 120)
    _text(plan.get("logline"), "logline", 20, 600)
    expected = {(item["asset_id"], item["role"], item["label"]) for item in params["references"]}
    got_refs = plan.get("references")
    if not isinstance(got_refs, list):
        raise DirectorRefusal("plan_invalid", "references must be a list echoing the request.")
    got = set()
    for item in got_refs:
        if not isinstance(item, dict):
            raise DirectorRefusal("plan_invalid", "each reference must be an object.")
        key = (item.get("asset_id"), item.get("role"), item.get("label"))
        if key not in expected:
            raise DirectorRefusal("plan_invalid", "A reference was added, renamed or dropped. The binding was not repaired.")
        got.add(key)
    if got != expected:
        raise DirectorRefusal("plan_invalid", "The reference binding does not match the request. It was not repaired.")
    by_role = {}
    for asset_id, role, _label in expected:
        by_role.setdefault(role, set()).add(asset_id)
    shots = plan.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= 24:
        raise DirectorRefusal("plan_invalid", "shots must be a list of 1 to 24 items.")
    cursor = 0
    seen_picture = set()
    seen_style = set()
    cleaned = []
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise DirectorRefusal("plan_invalid", "each shot must be an object.")
        if shot.get("index") != index or shot.get("start_seconds") != cursor:
            raise DirectorRefusal("plan_invalid", "Shot timing is not contiguous from 0. It was not shifted to fit.")
        duration = shot.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            raise DirectorRefusal("plan_invalid", "each duration_seconds must be a positive integer.")
        picture = _id_list(shot.get("picture_asset_ids"), by_role, PICTURE_ROLES, "picture_asset_ids")
        style = _id_list(shot.get("style_asset_ids"), by_role, ("style_motion",), "style_asset_ids")
        if set(picture) & set(style):
            raise DirectorRefusal("plan_invalid", "One asset was bound as both picture and style.")
        seen_picture.update(picture)
        seen_style.update(style)
        cleaned.append({
            "index": index,
            "start_seconds": cursor,
            "duration_seconds": duration,
            "summary": _text(shot.get("summary"), "summary", 10, 400),
            "image_prompt": _text(shot.get("image_prompt"), "image_prompt", 20, 800),
            "video_prompt": _text(shot.get("video_prompt"), "video_prompt", 20, 800),
            "picture_asset_ids": picture,
            "style_asset_ids": style,
        })
        cursor += duration
    if cursor != 120:
        raise DirectorRefusal(
            "plan_invalid",
            "Shot durations sum to %s, not 120. No shot was added or stretched." % cursor,
        )
    required_picture = set().union(*(by_role.get(role, set()) for role in PICTURE_ROLES))
    if seen_picture != required_picture:
        raise DirectorRefusal("plan_invalid", "A cast, place or wardrobe id was not bound to a shot picture. It was not filled in.")
    if seen_style != by_role.get("style_motion", set()):
        raise DirectorRefusal("plan_invalid", "A style reference was missing or was used as a pictured person.")
    return {
        "title": plan["title"].strip(),
        "logline": plan["logline"].strip(),
        "duration_seconds": 120,
        "aspect_ratio": "9:16",
        "constraints": dict(CONSTRAINTS),
        "references": [{"asset_id": asset_id, "role": role, "label": label} for asset_id, role, label in params_order(params)],
        "shots": cleaned,
        "render_started": False,
        "film": None,
    }


def params_order(params: dict):
    for item in params["references"]:
        yield item["asset_id"], item["role"], item["label"]


def _id_list(value, by_role: dict, roles: tuple, field: str) -> list:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise DirectorRefusal("plan_invalid", "%s must be a list of asset ids." % field)
    allowed = set().union(*(by_role.get(role, set()) for role in roles))
    if any(item not in allowed for item in value):
        raise DirectorRefusal("plan_invalid", "%s names an asset that is not in that role." % field)
    if len(set(value)) != len(value):
        raise DirectorRefusal("plan_invalid", "%s repeats an asset id." % field)
    return list(value)


def _text(value, field: str, low: int, high: int) -> str:
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise DirectorRefusal("plan_invalid", "%s must be text of %s to %s characters." % (field, low, high))
    return value.strip()


def _first_object(text: str):
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted and char == "{":
            depth += 1
        elif not quoted and char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except ValueError:
                    return None
    return None
