"""Optional adapter for Imagination Studio by keys+Wasserman.

The Studio process is upstream. This module only reads its localhost API and
refuses a job when the Studio itself says the renderer is not installed.
It does not download weights and it does not start the Studio's own film director.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

PIN = "3926471d59bfe8a51a295b7bfdef6cda0b09e1de"
VERSION = "4.3.1"
REPO = "https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman"
DEFAULT_URL = "http://127.0.0.1:7870"

# Confirmed in app/server.py, app/swap.py, app/revise.py, app/director.py of tag v4.3.1,
# and against a live process of that checkout. Image-to-video is not one of these.
FAMILIES = (
    {
        "id": "studio.swap",
        "title": "Studio: замена персонажа в ролике",
        "endpoint": "POST /api/jobs",
        "kind_name": "swap",
        "submits": True,
        "summary": (
            "Official Character Swap. It repaints the largest person in one continuous shot with Ref2VA. "
            "It is not a transparent background cutout and it does not guarantee the same face or packaging text."
        ),
    },
    {
        "id": "studio.remove",
        "title": "Studio: убрать человека из ролика",
        "endpoint": "POST /api/jobs",
        "kind_name": "remove",
        "submits": True,
        "summary": (
            "Official Remove a person. The guide and the job are video inpainting, not a transparent PNG. "
            "Pixels outside the mask, including a face or clothes, can change. The CPU product cutout stays image.product_prep."
        ),
    },
    {
        "id": "studio.revise",
        "title": "Studio: restyle, extend или fix",
        "endpoint": "POST /api/revise",
        "kind_name": "revise",
        "submits": False,
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
        "summary": (
            "POST /api/slate/direct and GET /api/slate/direct/{project} exist in the Studio. "
            "Calling the first one starts the Studio's own director thread. This Director does not call it."
        ),
    },
)


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
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except ValueError:
            parsed = {"error": raw.decode("utf-8", errors="replace")[:300]}
        return exc.code, parsed


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
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
        report["error"] = str(exc)
    return report


def brief(report: dict) -> dict:
    actions = report.get("actions") if isinstance(report.get("actions"), dict) else {}
    remove = actions.get("remove") if isinstance(actions.get("remove"), dict) else {}
    swap = report.get("swap") if isinstance(report.get("swap"), dict) else {}
    brains = report.get("brains") if isinstance(report.get("brains"), dict) else {}
    revise = report.get("revise") if isinstance(report.get("revise"), dict) else {}
    state = report.get("state") if isinstance(report.get("state"), dict) else {}
    updates = report.get("updates") if isinstance(report.get("updates"), dict) else {}
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
        "revise_kinds": [item.get("kind") for item in revise.get("kinds") or [] if isinstance(item, dict)],
        "weights_downloaded_by_director": False,
        "pin": PIN,
        "expected_version": VERSION,
    }


def readiness(capability_id: str, report: dict) -> str:
    info = brief(report)
    if not info["reachable"]:
        return "studio_unavailable"
    if capability_id == "studio.slate_direct":
        return "studio_direct_not_submitted"
    if capability_id == "studio.swap":
        return "ready_to_confirm" if info["swap_ready"] else "studio_renderer_unavailable"
    if capability_id == "studio.remove":
        return "ready_to_confirm" if info["remove_ready"] else "studio_renderer_unavailable"
    if capability_id == "studio.revise":
        if not info["ref2va"]:
            return "studio_renderer_unavailable"
        return "studio_clip_required"
    return "studio_unavailable"
