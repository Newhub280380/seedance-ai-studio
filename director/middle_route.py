"""HTTP for the planning layer. No provider call is made here."""

from __future__ import annotations

import json
from datetime import datetime

from director.errors import DirectorRefusal
from director.middle import MiddleStore, approve, pick_trend, prepare


def handle(app, method: str, route: str, body: bytes, query: str = ""):
    if not route.startswith("/api/director/middle"):
        return None
    store = MiddleStore(app.data_dir)
    if method == "GET" and route == "/api/director/middle/trends":
        from urllib.parse import parse_qs
        text = (parse_qs(query).get("q") or [""])[0]
        return 200, {"ok": True, "trends": pick_trend(text, 5), "prompts_included": False}, "application/json"
    if method == "POST" and route == "/api/director/middle/plan":
        plan = prepare(_json(body))
        store.save(plan)
        return 200, {"ok": True, "plan": plan}, "application/json"
    if method == "POST" and route == "/api/director/middle/approve":
        payload = _json(body)
        plan = store.get(str(payload.get("plan_id") or ""))
        if plan is None:
            raise DirectorRefusal("not_found", "Plan not found", 404)
        moment = None
        if payload.get("now"):
            moment = datetime.fromisoformat(payload["now"])
        plan = approve(plan, str(payload.get("gate") or ""), moment)
        store.save(plan)
        return 200, {"ok": True, "plan": plan}, "application/json"
    parts = route.strip("/").split("/")
    if method == "GET" and len(parts) == 4 and parts[:3] == ["api", "director", "middle"]:
        plan = store.get(parts[3])
        if plan is None:
            raise DirectorRefusal("not_found", "Plan not found", 404)
        return 200, {"ok": True, "plan": plan}, "application/json"
    return 404, {"ok": False, "error": {"code": "not_found", "message": "No such middle route"}}, "application/json"


def _json(body: bytes) -> dict:
    if not body:
        return {}
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DirectorRefusal("bad_json", "Request body is not JSON", 400) from exc
    if not isinstance(value, dict):
        raise DirectorRefusal("bad_json", "JSON object required", 400)
    return value
