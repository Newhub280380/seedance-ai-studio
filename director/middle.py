"""Planning layer between a short wish and a video model.

Nothing here calls a provider or writes a picture. A launch card stays idle
until it is approved, and an expired card cannot be approved.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from director.errors import DirectorRefusal

VIEWS = (
    "front",
    "three_quarter_left",
    "three_quarter_right",
    "profile_left",
    "profile_right",
    "full_body",
    "back",
    "face",
)
CARD_MINUTES = 15
CHUNK_SECONDS = 15
DRAFT_RESOLUTION = "480p"
WEIGHTS = {
    "task_fit": 0.30,
    "quality": 0.20,
    "control": 0.15,
    "reliability": 0.15,
    "price": 0.10,
    "speed": 0.05,
    "continuity": 0.05,
}
# Figures published for one catalog's draft tier. Not a live quote.
DRAFT_TOKENS_PER_SECOND = 7.5
MODELS = {
    "seedance-2.5": {
        "edit": True,
        "reference": True,
        "duration": (4, 30),
        "draft": DRAFT_RESOLUTION,
        "finals": ("720p", "1080p"),
        "aliases": True,
        "scores": {"task_fit": 0.9, "quality": 0.9, "control": 0.85, "reliability": 0.7, "price": 0.4, "speed": 0.5, "continuity": 0.8},
    },
    "kling": {
        "edit": False,
        "reference": True,
        "duration": (5, 10),
        "draft": DRAFT_RESOLUTION,
        "finals": ("720p", "1080p"),
        "aliases": False,
        "scores": {"task_fit": 0.6, "quality": 0.8, "control": 0.5, "reliability": 0.75, "price": 0.55, "speed": 0.6, "continuity": 0.45},
    },
    "wan": {
        "edit": True,
        "reference": True,
        "duration": (5, 15),
        "draft": DRAFT_RESOLUTION,
        "finals": ("720p",),
        "aliases": True,
        "scores": {"task_fit": 0.7, "quality": 0.65, "control": 0.7, "reliability": 0.6, "price": 0.7, "speed": 0.7, "continuity": 0.6},
    },
}
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_TRENDS = None


def _now():
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat()


def load_trends() -> list[dict]:
    global _TRENDS
    if _TRENDS is None:
        path = Path(__file__).with_name("middle_trends.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        _TRENDS = payload["trends"]
    return _TRENDS


def pick_trend(query: str, limit: int = 5) -> list[dict]:
    words = [part for part in re.split(r"\W+", (query or "").lower()) if len(part) > 2]
    scored = []
    for item in load_trends():
        hay = " ".join([item.get("slug") or "", item.get("title") or "", " ".join(item.get("tags") or [])]).lower()
        score = sum(1 for word in words if word in hay)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("slug") or ""))
    return [dict(item) for _score, item in scored[:limit]]


def character_sheet(name: str, photo_ids: list[str]) -> dict:
    if not isinstance(name, str) or not _NAME.match(name):
        raise DirectorRefusal("sheet_name_rejected", "Sheet name is a short lowercase handle, used as @name.", 400)
    if not isinstance(photo_ids, list) or not 1 <= len(photo_ids) <= 16:
        raise DirectorRefusal("sheet_photos_rejected", "A character sheet takes 1 to 16 photo ids. None were dropped.", 400)
    if any(not isinstance(item, str) or not item for item in photo_ids):
        raise DirectorRefusal("sheet_photos_rejected", "Each photo id must be a string.", 400)
    slots = []
    for index, view in enumerate(VIEWS):
        slots.append({"view": view, "source_photo_id": photo_ids[index % len(photo_ids)], "rendered": False})
    return {
        "name": name,
        "alias": "@" + name,
        "photo_ids": list(photo_ids),
        "views": slots,
        "approved": False,
        "rendered": False,
    }


def video_mode(mode: str) -> dict:
    if mode == "edit":
        return {"mode": "edit", "field": "sourceVideo", "keeps_frames": True, "motion_only": False}
    if mode == "reference":
        return {"mode": "reference", "field": "referenceVideo", "keeps_frames": False, "motion_only": True}
    raise DirectorRefusal("video_mode_rejected", "Video mode is edit or reference.", 400)


def frame_first(duration_seconds: float) -> dict:
    if not isinstance(duration_seconds, (int, float)) or isinstance(duration_seconds, bool) or duration_seconds <= 0:
        raise DirectorRefusal("duration_rejected", "duration_seconds must be a positive number.", 400)
    duration = float(duration_seconds)
    points = [("first", 0.0), ("key", round(duration / 2, 2)), ("last", round(duration, 2))]
    return {
        "approved": False,
        "rendered": False,
        "frames": [{"role": role, "at_seconds": at, "still": None} for role, at in points],
    }


def mannequin_passes(alias: str, color: str = "green") -> dict:
    if not alias.startswith("@"):
        raise DirectorRefusal("alias_rejected", "The second pass names the approved sheet as @name.", 400)
    return {
        "rendered": False,
        "passes": [
            {
                "index": 1,
                "action": "replace_actor_with_mannequin",
                "suit": color,
                "keep": ["motion", "camera", "light", "timing"],
            },
            {
                "index": 2,
                "action": "replace_mannequin_with_character",
                "suit": color,
                "alias": alias,
                "note": "The original actor is not the replacement target.",
            },
        ],
    }


def score_models(want_edit: bool) -> list[dict]:
    ranked = []
    for name, card in MODELS.items():
        if want_edit and not card["edit"]:
            fit = card["scores"]["task_fit"] * 0.2
        else:
            fit = card["scores"]["task_fit"]
        scores = dict(card["scores"])
        scores["task_fit"] = round(fit, 3)
        total = round(sum(scores[key] * WEIGHTS[key] for key in WEIGHTS), 4)
        ranked.append({"model": name, "score": total, "weights": WEIGHTS, "scores": scores, "edit": card["edit"]})
    ranked.sort(key=lambda item: -item["score"])
    return ranked


def chunks(duration_seconds: float, limit: int = CHUNK_SECONDS) -> list[dict]:
    if duration_seconds <= 0:
        raise DirectorRefusal("duration_rejected", "duration_seconds must be positive.", 400)
    pieces = []
    cursor = 0.0
    index = 1
    while cursor < duration_seconds - 0.001:
        length = min(float(limit), round(duration_seconds - cursor, 2))
        pieces.append({
            "index": index,
            "start_seconds": round(cursor, 2),
            "duration_seconds": round(length, 2),
        })
        cursor = round(cursor + length, 2)
        index += 1
    if len(pieces) > 1 and pieces[-1]["duration_seconds"] < 4:
        pieces[-2]["duration_seconds"] = round(pieces[-2]["duration_seconds"] + pieces[-1]["duration_seconds"], 2)
        pieces.pop()
    return pieces


def compile_prompt(model: str, alias: str, mode: str, duration_seconds: float, wish: str, rules: list[str] | None = None) -> dict:
    card = MODELS.get(model)
    if card is None:
        raise DirectorRefusal("model_unknown", "No model card for %s. No prompt was invented for it." % model, 400)
    chosen = video_mode("edit" if mode == "edit" else "reference")
    low, high = card["duration"]
    if not low <= duration_seconds <= high:
        raise DirectorRefusal(
            "duration_rejected",
            "%s accepts %s to %s seconds. %s was not stretched." % (model, low, high, duration_seconds),
        )
    lines = [item.strip() for item in (rules or []) if isinstance(item, str) and item.strip()]
    preserve = [
        "Keep the approved person's age, face and proportions.",
        "Keep the source camera, timing and light." if chosen["keeps_frames"] else "Use the video only for motion and camera.",
    ] + lines
    replace = [
        "Replace only the marked %s stand-in with %s." % ("green", alias),
        "Do not bring the original actor back.",
    ]
    integrate = [
        "Match light direction, contact shadows and grain.",
        "No halo around the replaced person.",
    ]
    timeline = [{
        "start": "0.00",
        "end": "%.2f" % float(duration_seconds),
        "text": (wish or "").strip() or "The approved person performs the shot.",
    }]
    alias_line = "@Video1 motion. %s identity." % alias if card["aliases"] else "Identity file first, motion file second. No @ aliases on this model."
    text = "\n".join([
        "PRESERVE",
        *[("- " + line) for line in preserve],
        "REPLACE",
        *[("- " + line) for line in replace],
        "INTEGRATE",
        *[("- " + line) for line in integrate],
        "REFERENCES",
        "- " + alias_line,
        "TIMELINE",
        "- %s-%s %s" % (timeline[0]["start"], timeline[0]["end"], timeline[0]["text"]),
    ])
    return {
        "model": model,
        "order": ["preserve", "replace", "integrate"],
        "preserve": preserve,
        "replace": replace,
        "integrate": integrate,
        "timeline": timeline,
        "text": text,
        "aliases": card["aliases"],
    }


def launch_card(model: str, prompt: str, settings: dict, why: str, moment: datetime | None = None) -> dict:
    moment = moment or _now()
    seconds = float(settings.get("duration_seconds") or 0)
    return {
        "model": model,
        "prompt": prompt,
        "settings": settings,
        "why": why,
        "price_estimate": {
            "unit": "catalog_tokens_per_second",
            "draft_rate": DRAFT_TOKENS_PER_SECOND,
            "draft_tokens": round(DRAFT_TOKENS_PER_SECOND * seconds, 2),
            "live_quote": False,
        },
        "created_at": _stamp(moment),
        "expires_at": _stamp(moment + timedelta(minutes=CARD_MINUTES)),
        "approved": False,
    }


def draft_plan(duration_seconds: float, final_resolution: str = "720p") -> dict:
    return {
        "rendered": False,
        "draft": {"resolution": DRAFT_RESOLUTION, "duration_seconds": duration_seconds, "approved": False},
        "final": {"resolution": final_resolution, "uses_draft": True, "approved": False},
        "chunks": chunks(duration_seconds),
    }


def qa_plan() -> dict:
    return {
        "ran": False,
        "checks": ["duration", "black_frames", "frozen_frames", "silence", "loudness"],
        "loudness_target_lufs": -14,
    }


def recovery(reason: str) -> dict:
    if reason != "not_like":
        raise DirectorRefusal("recovery_rejected", "Recovery is only for not_like. The prompt was not rewritten.", 400)
    return {
        "rewrote_prompt": False,
        "levers": ["add_close_frame", "shorter_chunk", "one_person"],
    }


class MiddleStore:
    def __init__(self, root: Path):
        self.root = Path(root) / "middle"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, plan: dict) -> dict:
        path = self.root / (plan["id"] + ".json")
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan

    def get(self, plan_id: str) -> dict | None:
        path = self.root / (plan_id + ".json")
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def prepare(body: dict, moment: datetime | None = None) -> dict:
    if not isinstance(body, dict):
        raise DirectorRefusal("bad_request", "JSON object required", 400)
    wish = body.get("wish")
    if not isinstance(wish, str) or not wish.strip():
        raise DirectorRefusal("wish_required", "wish is required.", 400)
    photo = body.get("photo_asset_id")
    video = body.get("video_asset_id")
    if not isinstance(photo, str) or not photo or not isinstance(video, str) or not video:
        raise DirectorRefusal("references_required", "photo_asset_id and video_asset_id are required. Files are not read.", 400)
    name = body.get("name") or "person"
    duration = body.get("duration_seconds", 13)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise DirectorRefusal("duration_rejected", "duration_seconds must be a number.", 400)
    mode_name = body.get("video_mode") or "edit"
    mode = video_mode(mode_name)
    ranked = score_models(mode["keeps_frames"])
    model = ranked[0]["model"]
    sheet = character_sheet(name, [photo])
    parts = chunks(float(duration))
    compiled = compile_prompt(model, sheet["alias"], mode["mode"], parts[0]["duration_seconds"], wish.strip(), body.get("rules"))
    moment = moment or _now()
    card = launch_card(model, compiled["text"], {
        "mode": mode["mode"],
        "field": mode["field"],
        "resolution": DRAFT_RESOLUTION,
        "duration_seconds": float(duration),
        "aspect_ratio": body.get("aspect_ratio") or "9:16",
    }, "Best task fit for keeping the source frames." if mode["keeps_frames"] else "Motion reference does not keep the source frames.", moment)
    trends = pick_trend(wish, 3)
    plan = {
        "id": str(uuid.uuid4()),
        "wish": wish.strip(),
        "render_started": False,
        "generation_performed": False,
        "photo_asset_id": photo,
        "video_asset_id": video,
        "sheet": sheet,
        "video_mode": mode,
        "frames": frame_first(float(duration)),
        "mannequin": mannequin_passes(sheet["alias"]),
        "routing": ranked,
        "prompt": compiled,
        "draft": {**draft_plan(float(duration)), "chunks": parts},
        "launch": card,
        "trends": trends,
        "qa": qa_plan(),
        "recovery": None,
    }
    return plan


def approve(plan: dict, gate: str, moment: datetime | None = None) -> dict:
    moment = moment or _now()
    if gate == "sheet":
        plan["sheet"]["approved"] = True
    elif gate == "frames":
        if not plan["sheet"]["approved"]:
            raise DirectorRefusal("sheet_not_approved", "Approve the character sheet before the frames.")
        plan["frames"]["approved"] = True
    elif gate == "launch":
        if not plan["frames"]["approved"]:
            raise DirectorRefusal("frames_not_approved", "Approve the stills before the launch card.")
        expires = datetime.fromisoformat(plan["launch"]["expires_at"])
        if moment > expires:
            raise DirectorRefusal("card_expired", "The launch card expired. It was not refreshed into a running job.")
        plan["launch"]["approved"] = True
        plan["render_started"] = False
    else:
        raise DirectorRefusal("gate_rejected", "Gate is sheet, frames or launch.", 400)
    return plan
