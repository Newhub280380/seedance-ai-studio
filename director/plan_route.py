"""Text-only shot plan from the operator's local OpenAI-compatible brain.

llama.cpp's documented chat field is response_format.type=json_schema with the
schema at json_schema.schema. It constrains tokens; it is not a written film.
This module does not call /api/slate/direct and does not replace a bad reply.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from director.errors import DirectorRefusal

ROLES = ("cast", "place", "wardrobe", "style_motion")
PICTURE_ROLES = ("cast", "place", "wardrobe")
BEATS = ("cafe", "dream", "prepare", "training", "performance", "family_ending")
CONSTRAINTS = {
    "age_preserved": True,
    "identity_preserved": True,
    "fake_encounter": False,
    "endorsement": False,
    "adult_body_face_paste": False,
}
MAX_ATTEMPTS = 2
DEFAULT_MAX_TOKENS = 4096

SYSTEM = (
    "Reply with one JSON object and no other text. Planning only: do not render. "
    "Top-level keys: title, logline, duration_seconds, aspect_ratio, constraints, references, shots, render_started. "
    "duration_seconds is the integer 120. aspect_ratio is the string 9:16. render_started is false. "
    "constraints is exactly "
    '{"age_preserved": true, "identity_preserved": true, "fake_encounter": false, '
    '"endorsement": false, "adult_body_face_paste": false}. '
    "Echo references with the same asset_id, role and label. "
    "Each shot uses index, start_seconds, duration_seconds, beat, summary, image_prompt, video_prompt, "
    "picture_asset_ids, style_asset_ids. The key is start_seconds, not start. "
    "Beats in order: cafe, dream, prepare, training, performance, family_ending. "
    "The first shot is cafe and the last is family_ending. Every beat appears at least once. A beat never goes backwards. "
    "Write a different image_prompt and video_prompt for every shot. "
    "picture_asset_ids are only cast, place and wardrobe. style_motion ids go only in style_asset_ids. "
    "Keep the cast ages and identities. A style reference is not a person in the scene, not a meeting, "
    "and not a body that receives someone else's face."
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title", "logline", "duration_seconds", "aspect_ratio", "constraints",
        "references", "shots", "render_started",
    ],
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "duration_seconds": {"type": "integer", "enum": [120]},
        "aspect_ratio": {"type": "string", "enum": ["9:16"]},
        "render_started": {"type": "boolean"},
        "constraints": {
            "type": "object",
            "additionalProperties": False,
            "required": list(CONSTRAINTS),
            "properties": {key: {"type": "boolean"} for key in CONSTRAINTS},
        },
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["asset_id", "role", "label"],
                "properties": {
                    "asset_id": {"type": "string"},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "label": {"type": "string"},
                },
            },
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "index", "start_seconds", "duration_seconds", "beat", "summary",
                    "image_prompt", "video_prompt", "picture_asset_ids", "style_asset_ids",
                ],
                "properties": {
                    "index": {"type": "integer"},
                    "start_seconds": {"type": "integer"},
                    "duration_seconds": {"type": "integer"},
                    "beat": {"type": "string", "enum": list(BEATS)},
                    "summary": {"type": "string"},
                    "image_prompt": {"type": "string"},
                    "video_prompt": {"type": "string"},
                    "picture_asset_ids": {"type": "array", "items": {"type": "string"}},
                    "style_asset_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


def schema_enabled() -> bool:
    return os.environ.get("DIRECTOR_BRAIN_JSON_SCHEMA", "1") != "0"


def thinking_disabled() -> bool:
    return os.environ.get("DIRECTOR_BRAIN_THINKING", "0") != "1"


def max_tokens() -> int:
    raw = os.environ.get("DIRECTOR_BRAIN_MAX_TOKENS") or str(DEFAULT_MAX_TOKENS)
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_TOKENS
    return value if 256 <= value <= 8192 else DEFAULT_MAX_TOKENS


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


def messages(params: dict, rejection: str | None = None) -> list[dict]:
    payload = {
        "brief": params["brief"],
        "duration_seconds": 120,
        "aspect_ratio": "9:16",
        "references": params["references"],
        "image_bytes_included": False,
        "render": False,
        "output_keys": list(SCHEMA["required"]),
        "shot_keys": list(SCHEMA["properties"]["shots"]["items"]["required"]),
        "beats_in_order": list(BEATS),
    }
    user = json.dumps(payload, ensure_ascii=False)
    if rejection:
        user += (
            "\n\nThe previous reply was rejected: "
            + rejection
            + " Return one JSON object that fixes only those errors. Do not replace it with a different film."
        )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]


def request_body(model: str, messages_value: list[dict], use_schema: bool) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens(),
        "messages": messages_value,
    }
    if use_schema:
        # OpenAI form. llama.cpp reads the schema from response_format.json_schema.schema.
        # A schema placed directly under response_format is ignored and is not used here.
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "director_plan", "strict": True, "schema": SCHEMA},
        }
    if thinking_disabled():
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


def chat(base: str, model: str, messages_value: list[dict], timeout: int, transport=None, use_schema: bool = True) -> tuple[int, dict]:
    url = base.rstrip("/") + "/chat/completions"
    body = request_body(model, messages_value, use_schema)
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
            parsed = {"error": raw.decode("utf-8", errors="replace")[:500]}
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


def diagnose(plan) -> dict:
    if not isinstance(plan, dict):
        return {"json_type": type(plan).__name__}
    shots = plan.get("shots") if isinstance(plan.get("shots"), list) else []
    durations = []
    prompts = []
    for shot in shots:
        if isinstance(shot, dict) and isinstance(shot.get("duration_seconds"), int) and not isinstance(shot.get("duration_seconds"), bool):
            durations.append(shot["duration_seconds"])
        if isinstance(shot, dict) and isinstance(shot.get("image_prompt"), str):
            prompts.append(" ".join(shot["image_prompt"].split()))
    return {
        "top_level_keys": sorted(str(key) for key in plan.keys()),
        "duration_seconds": plan.get("duration_seconds", "<missing>"),
        "aspect_ratio": plan.get("aspect_ratio", "<missing>"),
        "shot_count": len(shots),
        "shot_keys": sorted({str(key) for shot in shots if isinstance(shot, dict) for key in shot.keys()}),
        "duration_sum": sum(durations),
        "repeated_image_prompt": len(prompts) != len(set(prompts)),
    }


def validate_plan(plan: dict, params: dict) -> dict:
    problems = _problems(plan, params)
    if problems:
        observed = diagnose(plan)
        raise DirectorRefusal(
            "plan_invalid",
            " ".join(problems[:12]) + " Observed keys: %s." % ", ".join(observed["top_level_keys"]),
        )
    return {
        "title": plan["title"].strip(),
        "logline": plan["logline"].strip(),
        "duration_seconds": 120,
        "aspect_ratio": "9:16",
        "constraints": dict(CONSTRAINTS),
        "references": [{"asset_id": asset_id, "role": role, "label": label} for asset_id, role, label in params_order(params)],
        "shots": _clean_shots(plan["shots"]),
        "render_started": False,
        "film": None,
    }


def params_order(params: dict):
    for item in params["references"]:
        yield item["asset_id"], item["role"], item["label"]


def _problems(plan, params: dict) -> list[str]:
    if not isinstance(plan, dict):
        return ["The brain JSON is not an object. No substitute plan was written."]
    problems = []
    if plan.get("render_started") is not False:
        problems.append("render_started is %r, required false." % plan.get("render_started", "<missing>"))
    if plan.get("duration_seconds") != 120:
        problems.append("duration_seconds is %r, required 120." % plan.get("duration_seconds", "<missing>"))
    if plan.get("aspect_ratio") != "9:16":
        problems.append("aspect_ratio is %r, required 9:16." % plan.get("aspect_ratio", "<missing>"))
    if plan.get("constraints") != CONSTRAINTS:
        problems.append("constraints are %r, required the identity rules unchanged." % plan.get("constraints", "<missing>"))
    _check_text(problems, plan.get("title"), "title", 1, 120)
    _check_text(problems, plan.get("logline"), "logline", 20, 600)
    expected = {(item["asset_id"], item["role"], item["label"]) for item in params["references"]}
    by_role = {}
    for asset_id, role, _label in expected:
        by_role.setdefault(role, set()).add(asset_id)
    got_refs = plan.get("references")
    if not isinstance(got_refs, list):
        problems.append("references is missing or not a list.")
        got = set()
    else:
        got = set()
        for item in got_refs:
            if not isinstance(item, dict):
                problems.append("a reference is not an object.")
                continue
            key = (item.get("asset_id"), item.get("role"), item.get("label"))
            if key not in expected:
                problems.append("reference %r was added, renamed or dropped." % (key,))
            else:
                got.add(key)
        if got != expected:
            problems.append("the reference binding does not match the request.")
    shots = plan.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= 24:
        problems.append("shots must be a list of 1 to 24 items.")
        return problems
    cursor = 0
    seen_picture = set()
    seen_style = set()
    seen_beats = []
    previous_image = None
    previous_video = None
    durations = []
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            problems.append("shot %s is not an object." % index)
            continue
        if "start_seconds" not in shot and "start" in shot:
            problems.append("shot %s has start=%r and no start_seconds. The key was not renamed." % (index, shot.get("start")))
        if shot.get("index") != index or shot.get("start_seconds") != cursor:
            problems.append(
                "shot %s index/start_seconds is %r/%r, required %s/%s. Timing was not shifted."
                % (index, shot.get("index"), shot.get("start_seconds", "<missing>"), index, cursor)
            )
        duration = shot.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
            problems.append("shot %s duration_seconds is %r." % (index, duration))
            duration = 0
        else:
            durations.append(duration)
        beat = shot.get("beat")
        if beat not in BEATS:
            problems.append("shot %s beat is %r, required one of %s." % (index, beat, ", ".join(BEATS)))
        elif seen_beats and BEATS.index(beat) < BEATS.index(seen_beats[-1]):
            problems.append("shot %s beat %s goes backwards after %s." % (index, beat, seen_beats[-1]))
        else:
            seen_beats.append(beat)
        image = _check_text(problems, shot.get("image_prompt"), "shot %s image_prompt" % index, 20, 800)
        video = _check_text(problems, shot.get("video_prompt"), "shot %s video_prompt" % index, 20, 800)
        _check_text(problems, shot.get("summary"), "shot %s summary" % index, 10, 400)
        if image and previous_image == image:
            problems.append("shot %s image_prompt repeats the previous shot." % index)
        if video and previous_video == video:
            problems.append("shot %s video_prompt repeats the previous shot." % index)
        previous_image, previous_video = image, video
        picture = _id_list(problems, shot.get("picture_asset_ids"), by_role, PICTURE_ROLES, "shot %s picture_asset_ids" % index)
        style = _id_list(problems, shot.get("style_asset_ids"), by_role, ("style_motion",), "shot %s style_asset_ids" % index)
        if set(picture) & set(style):
            problems.append("shot %s binds one asset as both picture and style." % index)
        seen_picture.update(picture)
        seen_style.update(style)
        cursor += duration
    if sum(durations) != 120:
        problems.append("Shot durations sum to %s, not 120. No shot was added or stretched." % sum(durations))
    if seen_beats and (seen_beats[0] != "cafe" or seen_beats[-1] != "family_ending" or set(seen_beats) != set(BEATS)):
        problems.append("beats are %s. Required order starts at cafe, ends at family_ending, and includes every beat." % seen_beats)
    required_picture = set().union(*(by_role.get(role, set()) for role in PICTURE_ROLES)) if by_role else set()
    if required_picture and seen_picture != required_picture:
        problems.append("a cast, place or wardrobe id was not bound to a shot picture.")
    if seen_style != by_role.get("style_motion", set()):
        problems.append("a style reference was missing or was used as a pictured person.")
    return problems


def _clean_shots(shots: list) -> list:
    cleaned = []
    for shot in shots:
        cleaned.append({
            "index": shot["index"],
            "start_seconds": shot["start_seconds"],
            "duration_seconds": shot["duration_seconds"],
            "beat": shot["beat"],
            "summary": shot["summary"].strip(),
            "image_prompt": shot["image_prompt"].strip(),
            "video_prompt": shot["video_prompt"].strip(),
            "picture_asset_ids": list(shot["picture_asset_ids"]),
            "style_asset_ids": list(shot["style_asset_ids"]),
        })
    return cleaned


def _id_list(problems: list, value, by_role: dict, roles: tuple, field: str) -> list:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        problems.append("%s must be a list of asset ids." % field)
        return []
    allowed = set().union(*(by_role.get(role, set()) for role in roles))
    if any(item not in allowed for item in value):
        problems.append("%s names an asset that is not in that role." % field)
    if len(set(value)) != len(value):
        problems.append("%s repeats an asset id." % field)
    return list(value)


def _check_text(problems: list, value, field: str, low: int, high: int) -> str | None:
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        problems.append("%s must be text of %s to %s characters." % (field, low, high))
        return None
    return " ".join(value.split())


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
