import base64
import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from director.assets import AssetStore
from director.catalog_data import load_catalog, max_input_images
from director.comfy_gate import inspect_templates
from director.errors import DirectorRefusal, TransportFailure
from director.h3_lanes import COMMERCIAL, hosted_max_spec, static_lanes
from director import ltx_route, studio_route
from director.pngutil import solid_png
from director.prep_route import (
    ALPHA_BBOX_PAD,
    ALPHA_BBOX_THRESHOLD,
    LIMITS as PREP_LIMITS,
    alpha_object_box,
    assert_object_long_side,
    build_argv,
    gallery_chrome_crop,
    onnx_fallback,
    resolve_command,
    run_prep_command,
    validate_png,
)
from director.providers import MinimaxClient, VeniceClient
from director.skills import load_skills, skills_ready
from director.store import TaskStore

MAX_POLLS = 6
PRODUCT_GUARD = " Keep the product, its label and colors unchanged."
BLOCKED_IDENTITY = {"pixel", "pixel_identity", "face_restore", "restore_identity", "fully_preserved"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _data_url(asset):
    encoded = base64.b64encode(asset["data"]).decode("ascii")
    return "data:%s;base64,%s" % (asset["media_type"], encoded)


class DirectorApp:
    def __init__(self, repo_root, data_dir, venice_transport, minimax_transport, venice_api_key="", minimax_api_key=""):
        self.repo_root = Path(repo_root)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(self.data_dir / "tasks")
        self.assets = AssetStore(self.data_dir / "assets")
        self.results = self.data_dir / "results"
        self.results.mkdir(exist_ok=True)
        self.vendor = self.repo_root / "director" / "vendor"
        self.skills = load_skills(self.vendor)
        self.comfy = inspect_templates(self.vendor)
        self.comfy_by_id = {item["id"]: item for item in self.comfy}
        self.catalog, self.models = load_catalog(self.repo_root / "director" / "catalog" / "public_subset.json")
        self.venice_api_key = venice_api_key or ""
        self.minimax_api_key = minimax_api_key or ""
        self.venice = VeniceClient(venice_transport, self.venice_api_key)
        self.minimax = MinimaxClient(minimax_transport, self.minimax_api_key)
        self.venice_transport = venice_transport
        self.minimax_transport = minimax_transport
        self.prep_photo_command = os.environ.get("DIRECTOR_PREP_PHOTO") or "prep_photo"
        self.prep_timeout = int(os.environ.get("DIRECTOR_PREP_TIMEOUT") or "900")
        self.ltx_model_root = os.environ.get("LTX_MODEL_ROOT") or ""
        self.ltx_python = os.environ.get("LTX_PYTHON") or "python3"
        self.ltx_timeout = int(os.environ.get("LTX_TIMEOUT") or "3600")
        self.studio_url = os.environ.get("DIRECTOR_STUDIO_URL") or studio_route.DEFAULT_URL
        self.studio_transport = None

    def capabilities(self):
        items = []
        for spec in self._specs():
            items.append(self._public_capability(spec))
        for lane in static_lanes():
            items.append(self._public_lane(lane))
        for report in self.comfy:
            items.append(self._public_comfy(report))
        return {
            "generation_performed": False,
            "catalog": {
                "source": self.catalog.get("source"),
                "fetched_at": self.catalog.get("fetched_at"),
                "endpoint": self.catalog.get("endpoint"),
                "note": self.catalog.get("note"),
                "full_catalog_counts_this_fetch": self.catalog.get("full_catalog_counts_this_fetch"),
            },
            "h3_commercial": COMMERCIAL,
            "credentials": {
                "VENICE_API_KEY": "present" if self.venice_api_key else "missing",
                "MINIMAX_API_KEY": "present" if self.minimax_api_key else "missing",
            },
            "capabilities": items,
        }

    def put_asset(self, data, media_type):
        if media_type not in ("image/png", "image/jpeg", "image/webp"):
            raise DirectorRefusal("unsupported_media_type", "This slice stores png, jpeg or webp originals.", 400)
        return self.assets.put(data, media_type)

    def solid_fixture(self):
        record = self.assets.put(solid_png(), "image/png")
        record["synthetic"] = True
        record["note"] = "Solid 256x256 PNG generated in-process. Not a photograph and not a model output."
        return record

    def create_task(self, body):
        if not isinstance(body, dict):
            raise DirectorRefusal("bad_request", "JSON object required", 400)
        client = body.get("client_request_id")
        if not isinstance(client, str) or not client.strip():
            raise DirectorRefusal(
                "client_request_id_required",
                "client_request_id is required so a retry cannot submit a second billed job.",
                400,
            )
        existing = self.store.find_client(client.strip())
        if existing:
            return existing
        spec = self._spec_or_lane(body.get("capability"))
        if spec is None:
            raise DirectorRefusal("unknown_capability", "Unknown capability. The full Venice catalog is not loaded.", 404)
        provider = body.get("provider") or spec.get("provider")
        if provider != spec.get("provider"):
            raise DirectorRefusal(
                "provider_mismatch",
                "That provider is not this capability. The request was not sent to a different provider.",
                409,
            )
        self._reject_identity(body)
        task = self._blank(body, spec)
        if spec.get("kind") == "h3_refused":
            task["status"] = "refused"
            task["error"] = {
                "code": spec["run_status"],
                "message": spec.get("reason") or "This H3 lane is recorded and is not executed here.",
            }
            task["lane"] = spec.get("lane")
            return self.store.save(task)
        if spec.get("kind") == "comfy":
            task["status"] = "refused"
            task["error"] = {
                "code": "external_runner_required",
                "message": spec["commercial_reason"] + " Installing the JSON is not deployed inference.",
            }
            task["comfy"] = {key: spec[key] for key in ("lane", "weight_filenames", "labels", "runner", "commercial_runtime")}
            return self.store.save(task)
        try:
            if spec["provider"] == "venice":
                problems = skills_ready(self.skills, spec["skills"])
                if problems:
                    raise DirectorRefusal("skill_integrity", "Skill instructions failed the hash or endpoint check: " + ", ".join(problems))
            prepared = self._prepare(spec, body)
        except DirectorRefusal as exc:
            task["status"] = "refused"
            task["error"] = {"code": exc.code, "message": exc.message}
            saved = self.store.save(task)
            exc.task = saved
            raise
        task["params"] = prepared
        task["model"] = prepared.get("model")
        if spec["kind"] in ("video_reference", "video_venice_camera", "explainer"):
            task = self._quote(task, spec)
            if task["status"] != "quoted" or not body.get("confirm"):
                return task
            return self._execute(task, spec)
        if not body.get("confirm"):
            task["status"] = "draft"
            task["error"] = None
            return self.store.save(task)
        return self._execute(task, spec)

    def confirm(self, task_id, continue_remaining=False):
        task = self.store.get(task_id)
        if task is None:
            raise DirectorRefusal("not_found", "Task not found", 404)
        if task["status"] in ("completed", "queued", "processing", "failed", "refused", "submit_uncertain", "cancelled", "cancel_requested"):
            return task
        if task["status"] == "partial" and not continue_remaining:
            return task
        spec = self._spec_or_lane(task["capability"])
        task["confirm"] = True
        if spec["kind"] == "explainer" and continue_remaining:
            task["params"]["continue_remaining"] = True
        elif spec["kind"] == "explainer":
            task["params"]["continue_remaining"] = False
        return self._execute(task, spec)

    def poll(self, task_id):
        task = self.store.get(task_id)
        if task is None:
            raise DirectorRefusal("not_found", "Task not found", 404)
        if task["status"] in ("draft", "quoted", "refused", "failed", "completed", "cancelled", "cancel_requested", "submit_uncertain", "quote_failed", "quote_uncertain"):
            return task
        if task["capability"] == "explainer.project":
            return self._poll_explainer(task)
        if not task.get("provider_task_id"):
            raise DirectorRefusal("poll_without_provider_task", "This task has no provider task id. It was not resubmitted.", 409)
        if task.get("poll_count", 0) >= task.get("max_polls", MAX_POLLS):
            task["error"] = {"code": "poll_budget_exhausted", "message": "Poll budget is spent. The provider job was not submitted again."}
            return self.store.save(task)
        task["poll_count"] = task.get("poll_count", 0) + 1
        task["polled_at"] = _now()
        self.store.save(task)
        if task["provider"] == "venice":
            return self._poll_venice(task)
        if task["provider"] == "minimax":
            return self._poll_minimax(task)
        task["error"] = {"code": "poll_not_supported", "message": "This lane has no provider poll."}
        return self.store.save(task)

    def cancel(self, task_id):
        task = self.store.get(task_id)
        if task is None:
            raise DirectorRefusal("not_found", "Task not found", 404)
        if task["status"] in ("draft", "quoted", "quote_failed", "quote_uncertain", "refused"):
            task["status"] = "cancelled"
            task["error"] = {"code": "cancelled", "message": "Cancelled before a billed submit."}
            return self.store.save(task)
        if task["status"] in ("completed", "failed", "cancelled"):
            return task
        task["status"] = "cancel_requested"
        task["error"] = {
            "code": "cancel_requested",
            "message": "Local polling stopped. The task record is kept. No new generation was submitted.",
        }
        if task["provider"] == "minimax" and task.get("provider_task_id") and not task.get("cancel_attempted"):
            task["cancel_attempted"] = True
            self.store.save(task)
            try:
                result, payload = self.minimax._send("DELETE", "/v2/video_generation/" + task["provider_task_id"], None, 30)
            except TransportFailure as exc:
                task["error"] = {"code": "cancel_uncertain" if exc.uncertain else "cancel_failed", "message": str(exc)}
                return self.store.save(task)
            task.setdefault("provider_calls", []).append({"method": "DELETE", "path": "/v2/video_generation/{task_id}", "status": result.status})
            task["provider_cancel_status"] = result.status
            if isinstance(payload, dict):
                task["provider_cancel_body"] = payload
        return self.store.save(task)

    def read_result(self, task_id, index=None, role=None):
        task = self.store.get(task_id)
        if task is None:
            raise DirectorRefusal("not_found", "Task not found", 404)
        results = task.get("results") or ([] if not task.get("result") else [task["result"]])
        if not results:
            raise DirectorRefusal("no_result", "No stored result for this task", 404)
        if role:
            matches = [item for item in results if item.get("role") == role]
            if not matches:
                raise DirectorRefusal("no_result", "No stored result with role %s" % role, 404)
            item = matches[-1]
        elif index is not None:
            if index < 0 or index >= len(results):
                raise DirectorRefusal("no_result", "No stored result at index %s" % index, 404)
            item = results[index]
        elif task.get("capability") == "image.product_prep":
            finals = [item for item in results if item.get("role") == "output"]
            if not finals:
                raise DirectorRefusal(
                    "final_not_ready",
                    "The 3840 object output is not stored. The native cutout was not returned in its place.",
                    404,
                )
            item = finals[-1]
        else:
            item = results[0]
        path = self.results / item["file"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise DirectorRefusal("result_hash_mismatch", "Stored result no longer matches the recorded hash", 409)
        return data, item["media_type"]

    def _execute(self, task, spec):
        try:
            self._need_key(spec["provider"])
        except DirectorRefusal as exc:
            if task["status"] == "accepted":
                task["status"] = "draft"
            task["error"] = {"code": exc.code, "message": exc.message}
            saved = self.store.save(task)
            exc.task = saved
            raise
        kind = spec["kind"]
        if kind == "image_generate":
            return self._run_image_generate(task)
        if kind == "image_edit":
            return self._run_image_edit(task)
        if kind == "image_product":
            return self._run_product(task)
        if kind in ("video_reference", "video_venice_camera"):
            return self._run_video_queue(task)
        if kind == "audio_speech":
            return self._run_speech(task)
        if kind == "explainer":
            return self._run_explainer(task)
        if kind == "h3_max_hosted":
            return self._run_max(task)
        if kind == "product_prep":
            return self._run_product_prep(task)
        if kind == "ltx":
            return self._run_ltx(task, spec)
        if kind == "studio":
            return self._run_studio(task, spec)
        raise DirectorRefusal("not_executable", "This capability has no executor", 409)

    def _quote(self, task, spec):
        try:
            self._need_key(spec["provider"])
        except DirectorRefusal as exc:
            task["status"] = "draft"
            task["error"] = {"code": exc.code, "message": exc.message}
            saved = self.store.save(task)
            exc.task = saved
            raise
        task["status"] = "quoting"
        self.store.save(task)
        try:
            if spec["kind"] == "explainer":
                for scene in task["params"]["scenes"]:
                    payload = {
                        "model": task["params"]["video_model"],
                        "duration": scene["duration"],
                        "aspect_ratio": task["params"]["aspect_ratio"],
                        "resolution": task["params"]["resolution"],
                    }
                    result, body = self.venice.post("/api/v1/video/quote", payload)
                    task.setdefault("provider_calls", []).append({"path": "/api/v1/video/quote", "status": result.status, "scene": scene["index"]})
                    if result.status != 200 or not isinstance(body, dict) or "quote" not in body:
                        task["status"] = "quote_failed"
                        task["error"] = {"code": "quote_failed", "message": "Video quote did not return a price. No clip was queued."}
                        return self.store.save(task)
                    scene["quote_usd"] = body["quote"]
                task["status"] = "quoted"
                task["error"] = None
                return self.store.save(task)
            payload = {
                "model": task["params"]["model"],
                "duration": task["params"]["duration"],
            }
            if task["params"].get("aspect_ratio"):
                payload["aspect_ratio"] = task["params"]["aspect_ratio"]
            if task["params"].get("resolution"):
                payload["resolution"] = task["params"]["resolution"]
            result, body = self.venice.post("/api/v1/video/quote", payload)
            task.setdefault("provider_calls", []).append({"path": "/api/v1/video/quote", "status": result.status})
            if result.status != 200 or not isinstance(body, dict) or "quote" not in body:
                task["status"] = "quote_failed"
                task["error"] = {"code": "quote_failed", "message": "Quote failed. The clip was not queued."}
                return self.store.save(task)
            task["params"]["quote_usd"] = body["quote"]
            task["status"] = "quoted"
            task["error"] = None
            return self.store.save(task)
        except TransportFailure as exc:
            task["status"] = "quote_uncertain" if exc.uncertain else "quote_failed"
            task["error"] = {"code": task["status"], "message": str(exc)}
            return self.store.save(task)

    def _run_image_generate(self, task):
        params = task["params"]
        payload = {"model": params["model"], "prompt": params["prompt"], "safe_mode": True, "format": "png"}
        if params.get("aspect_ratio"):
            payload["aspect_ratio"] = params["aspect_ratio"]
        if params.get("width"):
            payload["width"] = params["width"]
            payload["height"] = params["height"]
        return self._venice_image_json(task, "/api/v1/image/generate", payload)

    def _run_image_edit(self, task):
        asset = self._require_asset(task["params"]["original_asset_id"])
        payload = {
            "model": task["params"]["model"],
            "prompt": task["params"]["prompt"],
            "image": _data_url(asset),
            "safe_mode": True,
            "output_format": "png",
        }
        if task["params"].get("aspect_ratio"):
            payload["aspect_ratio"] = task["params"]["aspect_ratio"]
        task["params"]["original_sha256"] = asset["sha256"]
        return self._venice_image_binary(task, "/api/v1/image/edit", payload)

    def _run_product(self, task):
        params = task["params"]
        images = []
        original = self._require_asset(params["original_asset_id"])
        images.append(_data_url(original))
        hashes = [original["sha256"]]
        for asset_id in params["reference_asset_ids"]:
            asset = self._require_asset(asset_id)
            images.append(_data_url(asset))
            hashes.append(asset["sha256"])
        payload = {
            "modelId": params["model"],
            "prompt": params["prompt"],
            "images": images,
            "safe_mode": True,
            "output_format": "png",
        }
        if params.get("aspect_ratio"):
            payload["aspect_ratio"] = params["aspect_ratio"]
        if params.get("resolution"):
            payload["resolution"] = params["resolution"]
        task["params"]["sent_asset_sha256"] = hashes
        task["fidelity"] = "generative_edit_not_pixel_identity"
        return self._venice_image_binary(task, "/api/v1/image/multi-edit", payload)

    def _run_video_queue(self, task):
        params = task["params"]
        payload = {"model": params["model"], "prompt": params["prompt"], "duration": params["duration"]}
        if params.get("aspect_ratio"):
            payload["aspect_ratio"] = params["aspect_ratio"]
        if params.get("resolution"):
            payload["resolution"] = params["resolution"]
        if params.get("image_asset_id"):
            payload["image_url"] = _data_url(self._require_asset(params["image_asset_id"]))
        if params.get("reference_asset_ids"):
            payload["reference_image_urls"] = [_data_url(self._require_asset(asset_id)) for asset_id in params["reference_asset_ids"]]
        if params.get("camera_trajectory"):
            payload["camera_trajectory"] = params["camera_trajectory"]
        return self._queue_venice(task, payload)

    def _run_speech(self, task):
        payload = {
            "model": task["params"]["model"],
            "voice": task["params"]["voice"],
            "input": task["params"]["text"],
            "response_format": "mp3",
        }
        task["status"] = "submitting"
        self.store.save(task)
        try:
            result, body = self.venice.post("/api/v1/audio/speech", payload)
        except TransportFailure as exc:
            return self._transport_failed(task, exc)
        task.setdefault("provider_calls", []).append({"path": "/api/v1/audio/speech", "status": result.status})
        media = result.headers.get("content-type", "")
        if result.status == 200 and media.startswith("audio/"):
            self._attach_bytes(task, result.body, media.split(";")[0])
            task["status"] = "completed"
            task["error"] = None
            return self.store.save(task)
        task["status"] = "failed"
        task["error"] = {"code": "speech_failed", "message": _error_text(result, body)}
        return self.store.save(task)

    def _run_explainer(self, task):
        params = task["params"]
        if params.get("continue_remaining"):
            indexes = [scene["index"] for scene in params["scenes"] if not scene.get("video_task_id")]
        elif not params["scenes"][0].get("video_task_id"):
            indexes = [0]
        else:
            indexes = []
        for index in indexes:
            scene = params["scenes"][index]
            if not scene.get("speech_task_id"):
                speech = self._child_speech(task, scene)
                scene["speech_task_id"] = speech["id"]
                self.store.save(task)
                if speech["status"] != "completed":
                    task["status"] = "partial"
                    task["error"] = {"code": "scene_speech_failed", "message": "Narration failed. Later scenes were not queued."}
                    return self.store.save(task)
            if not scene.get("video_task_id"):
                video = self._child_video(task, scene)
                scene["video_task_id"] = video["id"]
                self.store.save(task)
                if video["status"] not in ("queued", "processing", "completed"):
                    task["status"] = "partial"
                    task["error"] = {"code": "scene_video_failed", "message": "The clip was not queued. The narration task was kept."}
                    return self.store.save(task)
        pending = [scene for scene in params["scenes"] if not scene.get("video_task_id")]
        task["status"] = "awaiting_remaining_scenes" if pending else "processing"
        task["assembly"] = {
            "status": "not_run",
            "reason": "Local ffmpeg assembly runs only after every scene file exists and a separate assemble confirm is added. It was not run.",
        }
        task["error"] = None
        return self.store.save(task)

    def _child_speech(self, parent, scene):
        client = parent["client_request_id"] + ":speech:%s" % scene["index"]
        existing = self.store.find_client(client)
        if existing:
            return existing
        child = self._blank({"client_request_id": client, "capability": "audio.speech", "confirm": True}, self._spec("audio.speech"))
        child["parent_id"] = parent["id"]
        child["params"] = {"model": parent["params"]["speech_model"], "voice": parent["params"]["voice"], "text": scene["narration"]}
        child["model"] = child["params"]["model"]
        child = self.store.save(child)
        return self._run_speech(child)

    def _child_video(self, parent, scene):
        client = parent["client_request_id"] + ":video:%s" % scene["index"]
        existing = self.store.find_client(client)
        if existing:
            return existing
        child = self._blank({"client_request_id": client, "capability": "explainer.scene", "confirm": True}, {
            "provider": "venice",
            "kind": "video_reference",
            "skills": [],
        })
        child["capability"] = "explainer.scene"
        child["parent_id"] = parent["id"]
        child["params"] = {
            "model": parent["params"]["video_model"],
            "prompt": scene["prompt"] + " " + parent["params"]["style_line"],
            "duration": scene["duration"],
            "aspect_ratio": parent["params"]["aspect_ratio"],
            "resolution": parent["params"]["resolution"],
        }
        child["model"] = child["params"]["model"]
        child = self.store.save(child)
        return self._run_video_queue(child)

    def _run_max(self, task):
        params = task["params"]
        content = [{"type": "text", "text": params["prompt"]}]
        if params["mode"] == "first_frame":
            content.append({
                "type": "image_url",
                "image_url": {"url": _data_url(self._require_asset(params["original_asset_id"]))},
                "role": "first_frame",
            })
        elif params["mode"] == "reference":
            for asset_id in params["reference_asset_ids"]:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": _data_url(self._require_asset(asset_id))},
                    "role": "reference_image",
                })
        payload = {
            "model": "MiniMax-H3-Max",
            "content": content,
            "resolution": params["resolution"],
            "duration": params["duration"],
            "ratio": params["ratio"],
            "extra": {"prompt_expansion_mode": params["prompt_expansion_mode"]},
        }
        task["status"] = "submitting"
        task["model"] = "MiniMax-H3-Max"
        self.store.save(task)
        try:
            result, body = self.minimax.post("/v2/video_generation", payload)
        except TransportFailure as exc:
            return self._transport_failed(task, exc)
        task.setdefault("provider_calls", []).append({"path": "/v2/video_generation", "status": result.status, "model": "MiniMax-H3-Max"})
        if result.status != 200 or not isinstance(body, dict) or not body.get("task_id"):
            task["status"] = "failed"
            task["error"] = {"code": "max_submit_failed", "message": _error_text(result, body)}
            return self.store.save(task)
        task["provider_task_id"] = str(body["task_id"])
        task["status"] = "queued"
        task["error"] = None
        task["lane"] = "hosted_max"
        return self.store.save(task)

    def _queue_venice(self, task, payload):
        task["status"] = "submitting"
        self.store.save(task)
        try:
            result, body = self.venice.post("/api/v1/video/queue", payload)
        except TransportFailure as exc:
            return self._transport_failed(task, exc)
        task.setdefault("provider_calls", []).append({"path": "/api/v1/video/queue", "status": result.status, "model": payload.get("model")})
        if result.status != 200 or not isinstance(body, dict) or not body.get("queue_id"):
            task["status"] = "failed"
            task["error"] = {"code": "queue_failed", "message": _error_text(result, body)}
            return self.store.save(task)
        task["provider_task_id"] = body["queue_id"]
        if body.get("download_url"):
            task["download_url"] = body["download_url"]
        task["status"] = "queued"
        task["error"] = None
        return self.store.save(task)

    def _venice_image_json(self, task, path, payload):
        task["status"] = "submitting"
        self.store.save(task)
        try:
            result, body = self.venice.post(path, payload)
        except TransportFailure as exc:
            return self._transport_failed(task, exc)
        task.setdefault("provider_calls", []).append({"path": path, "status": result.status, "model": payload.get("model")})
        violation = (result.headers.get("x-venice-is-content-violation") or "").lower() == "true"
        if result.status == 200 and isinstance(body, dict) and body.get("images") and not violation:
            task["results"] = []
            for index, image in enumerate(body["images"]):
                raw = base64.b64decode(image)
                task["results"].append(self._write_result(task["id"] + "-" + str(index), raw, "image/png"))
            task["result"] = task["results"][0]
            task["status"] = "completed"
            task["provider_request_id"] = body.get("id")
            task["error"] = None
            return self.store.save(task)
        task["status"] = "failed"
        code = "content_violation" if violation else "generate_failed"
        task["error"] = {"code": code, "message": _error_text(result, body)}
        return self.store.save(task)

    def _venice_image_binary(self, task, path, payload):
        task["status"] = "submitting"
        self.store.save(task)
        redacted = {key: payload[key] for key in payload if key not in ("image", "images")}
        if "images" in payload:
            redacted["image_count"] = len(payload["images"])
        task["request_redacted"] = redacted
        try:
            result, body = self.venice.post(path, payload)
        except TransportFailure as exc:
            return self._transport_failed(task, exc)
        task.setdefault("provider_calls", []).append({
            "path": path,
            "status": result.status,
            "model": payload.get("model") or payload.get("modelId"),
        })
        media = (result.headers.get("content-type") or "").split(";")[0]
        if result.status == 200 and media.startswith("image/"):
            self._attach_bytes(task, result.body, media)
            task["status"] = "completed"
            task["error"] = None
            return self.store.save(task)
        task["status"] = "failed"
        task["error"] = {"code": "edit_failed", "message": _error_text(result, body)}
        return self.store.save(task)

    def _poll_venice(self, task):
        payload = {
            "model": task["model"],
            "queue_id": task["provider_task_id"],
            "delete_media_on_completion": True,
        }
        try:
            result, body = self.venice.post("/api/v1/video/retrieve", payload)
        except TransportFailure as exc:
            task["error"] = {"code": "poll_failed", "message": str(exc)}
            if task["status"] == "queued":
                task["status"] = "processing"
            return self.store.save(task)
        task.setdefault("provider_calls", []).append({"path": "/api/v1/video/retrieve", "status": result.status})
        media = (result.headers.get("content-type") or "").split(";")[0]
        if result.status == 200 and media.startswith("video/"):
            self._attach_bytes(task, result.body, media)
            task["status"] = "completed"
            task["error"] = None
            return self.store.save(task)
        if result.status == 200 and isinstance(body, dict) and body.get("status") == "PROCESSING":
            task["status"] = "processing"
            task["provider_status"] = body
            task["error"] = None
            return self.store.save(task)
        if result.status == 200 and isinstance(body, dict) and body.get("status") == "COMPLETED" and task.get("download_url"):
            return self._download_url(task, task["download_url"])
        if result.status == 429:
            task["error"] = {"code": "rate_limited", "message": "Poll was rate limited. The job was not submitted again."}
            return self.store.save(task)
        task["status"] = "failed"
        task["error"] = {"code": "provider_failed", "message": _error_text(result, body)}
        return self.store.save(task)

    def _poll_minimax(self, task):
        try:
            result, body = self.minimax.get("/v2/query/video_generation/" + task["provider_task_id"])
        except TransportFailure as exc:
            task["error"] = {"code": "poll_failed", "message": str(exc)}
            return self.store.save(task)
        task.setdefault("provider_calls", []).append({"path": "/v2/query/video_generation/{task_id}", "status": result.status})
        remote = body.get("task") if isinstance(body, dict) else None
        status = remote.get("status") if isinstance(remote, dict) else None
        if result.status == 200 and status in ("queued", "running"):
            task["status"] = "processing" if status == "running" else "queued"
            task["provider_status"] = status
            task["error"] = None
            return self.store.save(task)
        if result.status == 200 and status == "succeeded":
            url = ((remote.get("content") or {}).get("url"))
            task["download_url"] = url
            if not url:
                task["status"] = "failed"
                task["error"] = {"code": "missing_download_url", "message": "The hosted task succeeded without a download URL. It was not submitted again."}
                return self.store.save(task)
            return self._download_url(task, url)
        if result.status == 429:
            task["error"] = {"code": "rate_limited", "message": "Poll was rate limited. The job was not submitted again."}
            return self.store.save(task)
        task["status"] = "failed"
        task["provider_status"] = status
        task["error"] = {"code": "provider_failed", "message": _error_text(result, body)}
        return self.store.save(task)

    def _poll_explainer(self, task):
        for scene in task["params"]["scenes"]:
            video_id = scene.get("video_task_id")
            if not video_id:
                continue
            child = self.store.get(video_id)
            if child and child["status"] in ("queued", "processing"):
                updated = self.poll(video_id)
                scene["video_status"] = updated["status"]
                task["error"] = None
                return self.store.save(task)
        task["error"] = {"code": "nothing_to_poll", "message": "No queued child clip. Nothing was submitted."}
        return self.store.save(task)

    def _download_url(self, task, url):
        if task.get("download_fetched"):
            return task
        task["download_fetched"] = True
        self.store.save(task)
        try:
            result = self.minimax.get_absolute(url) if task["provider"] == "minimax" else self.venice.transport.request("GET", url, {}, None, 30)
        except TransportFailure as exc:
            task["status"] = "completed_remote"
            task["error"] = {"code": "download_failed", "message": str(exc) + " The provider task was not submitted again."}
            return self.store.save(task)
        media = (result.headers.get("content-type") or "application/octet-stream").split(";")[0]
        if result.status == 200 and result.body:
            self._attach_bytes(task, result.body, media)
            task["status"] = "completed"
            task["error"] = None
            return self.store.save(task)
        task["status"] = "completed_remote"
        task["error"] = {"code": "download_failed", "message": "Download failed. The provider task was not submitted again."}
        return self.store.save(task)

    def _prepare(self, spec, body):
        kind = spec["kind"]
        if kind == "image_generate":
            return self._prepare_image(body)
        if kind == "image_edit":
            return self._prepare_edit(body)
        if kind == "image_product":
            return self._prepare_product(body)
        if kind == "video_reference":
            return self._prepare_reference(body)
        if kind == "video_venice_camera":
            return self._prepare_camera(body)
        if kind == "audio_speech":
            return self._prepare_speech(body)
        if kind == "explainer":
            return self._prepare_explainer(body)
        if kind == "h3_max_hosted":
            return self._prepare_max(body)
        if kind == "product_prep":
            return self._prepare_product_prep(body)
        if kind == "ltx":
            return self._prepare_ltx(spec, body)
        if kind == "studio":
            return self._prepare_studio(spec, body)
        raise DirectorRefusal("not_executable", "No preparer for this capability")

    def _studio_probe(self):
        cached = getattr(self, "_studio_probe_cached", None)
        if cached is None:
            cached = studio_route.discover(self.studio_url, self.studio_transport)
            self._studio_probe_cached = cached
        return cached

    def _prepare_studio(self, spec, body):
        if body.get("upload_private_face"):
            raise DirectorRefusal(
                "private_face_refused",
                "A private face is not uploaded to Studio or to a cloud brain from this adapter.",
            )
        upstream = body.get("studio_params") or {}
        if not isinstance(upstream, dict):
            raise DirectorRefusal("studio_params_rejected", "studio_params must be an object.")
        return {
            "studio_url": self.studio_url,
            "endpoint": spec["endpoint"],
            "submits": spec["submits"],
            "note": spec["summary"],
            "same_face": "not_guaranteed",
            "transparent_background": False,
            "upstream_params": upstream,
        }

    def _run_studio(self, task, spec):
        report = self._studio_probe()
        info = studio_route.brief(report)
        task["studio"] = info
        if not info["reachable"]:
            task["status"] = "failed"
            task["error"] = {
                "code": "studio_unavailable",
                "message": "Imagination Studio is not answering at %s. %s" % (self.studio_url, info["error"]),
            }
            return self.store.save(task)
        if spec["id"] == "studio.slate_direct":
            task["status"] = "failed"
            task["error"] = {
                "code": "studio_direct_not_submitted",
                "message": "POST /api/slate/direct was not called. It would start the Studio's own director.",
            }
            return self.store.save(task)
        if spec["id"] == "studio.swap" and not info["swap_ready"]:
            task["status"] = "failed"
            task["error"] = {"code": "studio_renderer_unavailable", "message": info["swap_why"] or "Character Swap is not ready."}
            return self.store.save(task)
        if spec["id"] == "studio.remove" and not info["remove_ready"]:
            task["status"] = "failed"
            task["error"] = {"code": "studio_renderer_unavailable", "message": info["remove_why"] or "Remove a person is not ready."}
            return self.store.save(task)
        if spec["id"] == "studio.revise":
            task["status"] = "failed"
            task["error"] = {
                "code": "studio_renderer_unavailable" if not info["ref2va"] else "studio_clip_required",
                "message": "Revise was not submitted. Ref2VA is %s and no library clip was sent." % ("present" if info["ref2va"] else "absent"),
            }
            return self.store.save(task)
        if spec["id"] not in ("studio.swap", "studio.remove"):
            task["status"] = "failed"
            task["error"] = {"code": "studio_renderer_unavailable", "message": "This Studio operation is not submitted from Director."}
            return self.store.save(task)
        kind_name = "swap" if spec["id"] == "studio.swap" else "remove"
        call = self.studio_transport or studio_route.request_json
        status, payload = call("POST", self.studio_url.rstrip("/") + "/api/jobs", {"kind": kind_name, "params": task["params"].get("upstream_params") or {}}, 15)
        task.setdefault("provider_calls", []).append({"method": "POST", "path": "/api/jobs", "kind": kind_name, "http_status": status})
        if status >= 400 or not isinstance(payload, dict) or not payload.get("id"):
            task["status"] = "failed"
            message = payload.get("error") if isinstance(payload, dict) else str(payload)
            task["error"] = {"code": "studio_rejected", "message": str(message)[:500]}
            return self.store.save(task)
        task["status"] = "upstream_accepted"
        task["error"] = None
        task["upstream_job"] = {"id": payload.get("id"), "status": payload.get("status")}
        task["fidelity"] = "studio_generative_not_identity_lock"
        return self.store.save(task)

    def _ltx_probe(self):
        cached = getattr(self, "_ltx_probe_cached", None)
        if cached is None:
            cached = ltx_route.probe(self.ltx_model_root, self.ltx_python)
            self._ltx_probe_cached = cached
        return cached

    def _prepare_ltx(self, spec, body):
        if body.get("commercial_multiuser"):
            raise DirectorRefusal(
                "ltx_commercial_disabled",
                ltx_route.ATTACHMENT_A20 + " Paid multi-user use stays off. This is not a legal clearance of a subscription product.",
            )
        if spec["id"] == "ltx.distilled" and (body.get("video_path") or body.get("video_asset_id")):
            raise DirectorRefusal(
                "not_temporal_edit",
                "ltx.distilled is text or still-image to video. A source video is not accepted here and was not treated as object replacement. Use ltx.ic_lora or ltx.retake.",
            )
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise DirectorRefusal("prompt_required", "prompt is required", 400)
        frames = body.get("num_frames", 121)
        try:
            frames = int(frames)
        except (TypeError, ValueError) as exc:
            raise DirectorRefusal("frames_rejected", "num_frames must be an integer of the form 8k+1.") from exc
        if frames < 1 or (frames - 1) % 8 != 0:
            raise DirectorRefusal("frames_rejected", "num_frames must be 8k+1, for example 121.")
        images = []
        if body.get("image_asset_id"):
            images.append({"asset_id": body["image_asset_id"], "frame": int(body.get("frame", 0)), "strength": float(body.get("strength", 1.0))})
        for item in body.get("images") or []:
            if not isinstance(item, dict) or not item.get("asset_id"):
                raise DirectorRefusal("image_rejected", "Each keyframe needs an asset_id.")
            images.append({"asset_id": item["asset_id"], "frame": int(item.get("frame", 0)), "strength": float(item.get("strength", 1.0))})
        if spec["id"] == "ltx.ic_lora" and not body.get("video_path"):
            raise DirectorRefusal("video_conditioning_required", "ltx.ic_lora requires the source video. A still was not substituted for it.")
        if spec["id"] == "ltx.ic_lora" and not body.get("lora_path"):
            raise DirectorRefusal("lora_required", "ltx.ic_lora requires the path of an IC-LoRA under LTX_MODEL_ROOT.")
        if spec["id"] == "ltx.retake":
            if not body.get("video_path"):
                raise DirectorRefusal("video_required", "ltx.retake requires the source video.")
            start, end = body.get("start_time"), body.get("end_time")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start >= end:
                raise DirectorRefusal("retake_window_rejected", "start_time must be less than end_time.")
        offload = body.get("offload")
        if offload not in (None, "", "cpu", "disk"):
            raise DirectorRefusal("offload_rejected", "offload must be cpu or disk, matching the official CLI.")
        quant = body.get("quantization")
        if quant not in (None, "", "fp8-cast"):
            raise DirectorRefusal("quantization_rejected", "The only quantization flag wired here is fp8-cast.")
        return {
            "prompt": prompt.strip(),
            "num_frames": frames,
            "seed": int(body.get("seed", 42)),
            "images": images,
            "video_path": body.get("video_path"),
            "mask_path": body.get("mask_path"),
            "lora_path": body.get("lora_path"),
            "video_strength": body.get("video_strength", 1.0),
            "lora_strength": body.get("lora_strength", 1.0),
            "mask_strength": body.get("mask_strength", 1.0),
            "start_time": body.get("start_time"),
            "end_time": body.get("end_time"),
            "offload": offload or None,
            "quantization": quant or None,
            "enhance_prompt": False,
            "model_root": self.ltx_model_root,
            "license": {
                "code": "Apache-2.0",
                "weights": "LTX-2.x Community License",
                "attachment_a20": ltx_route.ATTACHMENT_A20,
                "revenue_note": "Section 2.1 requires a paid license at $10,000,000 annual revenue. Attachment A(20) is a separate competing-service limit and is not waived below that figure. This is not a legal verdict.",
                "commercial_multiuser": "disabled",
            },
        }

    def _run_ltx(self, task, spec):
        if task["params"].get("license", {}).get("commercial_multiuser") == "disabled" and False:
            pass
        report = self._ltx_probe()
        task["ltx"] = {
            "pin": ltx_route.PIN,
            "repo": ltx_route.REPO,
            "module": spec["module"],
            "temporal_edit": spec["temporal_edit"],
            "ready": report["ready"],
            "missing": report["missing"],
            "measured_hosts": report["measured_hosts"],
        }
        if not report["ready"]:
            task["status"] = "failed"
            task["error"] = {
                "code": "ltx_not_ready",
                "message": "LTX was not started. Missing: " + "; ".join(report["missing"]),
            }
            return self.store.save(task)
        if spec["id"] == "ltx.dfr":
            lora = Path(self.ltx_model_root) / ltx_route.DFR_LORA
            if not lora.is_file():
                task["status"] = "failed"
                task["error"] = {"code": "ltx_not_ready", "message": "Missing detailing IC-LoRA " + ltx_route.DFR_LORA}
                return self.store.save(task)
        try:
            paths = self._ltx_paths(task, spec)
        except DirectorRefusal as exc:
            task["status"] = "failed"
            task["error"] = {"code": exc.code, "message": exc.message}
            return self.store.save(task)
        work = self.data_dir / "ltx" / task["id"]
        work.mkdir(parents=True, exist_ok=True)
        output = work / "output.mp4"
        params = dict(task["params"])
        params["model_root"] = self.ltx_model_root
        argv = ltx_route.build_argv(self.ltx_python, spec, paths, output, params)
        task.setdefault("provider_calls", []).append({"argv": argv, "role": "ltx"})
        task["status"] = "running"
        self.store.save(task)
        try:
            code, stdout, stderr = ltx_route.run_once(argv, self.ltx_timeout)
        except DirectorRefusal as exc:
            task["status"] = "failed"
            task["error"] = {"code": exc.code, "message": exc.message}
            return self.store.save(task)
        task["ltx"]["returncode"] = code
        task["ltx"]["stderr_tail"] = stderr[-400:]
        if code != 0 or not ltx_route.is_mp4(output):
            task["status"] = "failed"
            task["error"] = {
                "code": "ltx_output_missing" if code == 0 else "ltx_failed",
                "message": "No MP4 was stored. exit %s. %s" % (code, stderr[-240:]),
            }
            return self.store.save(task)
        blob = output.read_bytes()
        stored = self.results / (task["id"] + "-ltx.mp4")
        stored.write_bytes(blob)
        task["result"] = {
            "role": "output",
            "file": stored.name,
            "media_type": "video/mp4",
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
        }
        task["results"] = [task["result"]]
        task["status"] = "completed"
        task["error"] = None
        task["fidelity"] = "ltx_generative_not_label_lock"
        return self.store.save(task)

    def _ltx_paths(self, task, spec):
        paths = {}
        images = []
        for item in task["params"]["images"]:
            asset = self._require_asset(item["asset_id"])
            folder = self.data_dir / "ltx" / task["id"]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("frame-%s.png" % item["frame"])
            target.write_bytes(asset["data"])
            images.append({"path": target, "frame": item["frame"], "strength": item["strength"]})
        task["params"]["images"] = images
        if task["params"].get("video_path"):
            paths["video"] = self._inside_data(task["params"]["video_path"])
        if task["params"].get("mask_path"):
            paths["mask"] = self._inside_data(task["params"]["mask_path"])
        if task["params"].get("lora_path"):
            root = Path(self.ltx_model_root).resolve()
            lora = Path(task["params"]["lora_path"]).resolve()
            if root not in lora.parents or not lora.is_file():
                raise DirectorRefusal("lora_missing", "IC-LoRA must be an existing file inside LTX_MODEL_ROOT.")
            paths["lora"] = lora
        return paths

    def _inside_data(self, raw):
        path = Path(raw).resolve()
        root = self.data_dir.resolve()
        if path != root and root not in path.parents:
            raise DirectorRefusal("path_outside_data", "That file is outside the Director data directory.")
        if not path.is_file():
            raise DirectorRefusal("input_missing", "Input file is missing: " + path.name)
        return path

    def _prepare_product_prep(self, body):
        if body.get("model") not in (None, "", "u2net"):
            raise DirectorRefusal(
                "model_not_this_lane",
                "image.product_prep calls the existing prep_photo tool with rembg model u2net. Another model was not substituted.",
            )
        if body.get("upscale") not in (None, "", "onnx"):
            raise DirectorRefusal("upscale_rejected", "This prep route uses --upscale onnx. A different upscaler was not substituted.")
        if body.get("size") not in (None, "", 3840, "3840"):
            raise DirectorRefusal("size_rejected", "The long side for this route is 3840. Another size was not substituted.")
        return {
            "original_asset_id": self._one_asset(body.get("original_asset_id")),
            "model": "u2net",
            "size": 3840,
            "upscale": "onnx",
            "denoise": "none",
            "bg": "transparent",
            "format": "png",
            "prompt_used": False,
            "limits": PREP_LIMITS,
        }

    def _run_product_prep(self, task):
        command = resolve_command(self.prep_photo_command)
        if not command:
            task["status"] = "failed"
            task["error"] = {
                "code": "prep_command_missing",
                "message": "DIRECTOR_PREP_PHOTO is not an executable prep_photo. Nothing was sent to a paid API.",
            }
            return self.store.save(task)
        asset = self._require_asset(task["params"]["original_asset_id"])
        work = self.data_dir / "prep" / task["id"]
        work.mkdir(parents=True, exist_ok=True)
        source = work / "original"
        source.write_bytes(asset["data"])
        from PIL import Image
        with Image.open(source) as incoming:
            incoming.load()
            cropped = gallery_chrome_crop(incoming)
            prep_input = incoming.convert("RGB") if cropped is None else cropped
        crop_path = work / "crop.png"
        prep_input.save(crop_path, format="PNG")
        task["prep"] = {
            "command": command,
            "gallery_chrome_crop": cropped is not None,
            "crop_size": list(prep_input.size),
            "limits": PREP_LIMITS,
        }
        task["status"] = "running"
        self.store.save(task)
        cutout = work / "cutout.png"
        output = work / "output.png"
        stored = []
        try:
            stored.append(self._invoke_prep(
                task,
                command,
                crop_path,
                cutout,
                upscale="none",
                size=0,
                expect_long_side=max(prep_input.size),
                source_size=prep_input.size,
                role="cutout",
            ))
            task["results"] = list(stored)
            self.store.save(task)
            from PIL import Image as _Image
            with _Image.open(cutout) as cut_image:
                cut_image.load()
                box = alpha_object_box(cut_image, ALPHA_BBOX_THRESHOLD, ALPHA_BBOX_PAD)
                tight = cut_image.crop(box)
                native_size = list(cut_image.size)
            tight_path = work / "tight.png"
            tight.save(tight_path, format="PNG")
            tight_bytes = tight_path.read_bytes()
            tight_stored = self.results / ("%s-tight.png" % task["id"])
            tight_stored.write_bytes(tight_bytes)
            stored.append({
                "role": "tight",
                "media_type": "image/png",
                "width": tight.size[0],
                "height": tight.size[1],
                "file": tight_stored.name,
                "sha256": hashlib.sha256(tight_bytes).hexdigest(),
                "bytes": len(tight_bytes),
            })
            tight_long = max(tight.size)
            task["prep"]["alpha_bbox"] = {
                "threshold": ALPHA_BBOX_THRESHOLD,
                "pad": ALPHA_BBOX_PAD,
                "box": list(box),
                "tight_size": list(tight.size),
                "native_cutout_size": native_size,
            }
            task["results"] = list(stored)
            self.store.save(task)
            if tight_long >= 3840:
                task["prep"]["upscale_skipped"] = "tight object long side is already %s; onnx was not run again" % tight_long
                output.write_bytes(tight_path.read_bytes())
                info = validate_png(output, expect_long_side=tight_long, source_size=tight.size)
                metrics = assert_object_long_side(output, tight_long)
                info.update({"role": "output", "media_type": "image/png", "object": metrics})
                info["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
                stored_path = self.results / ("%s-output.png" % task["id"])
                stored_path.write_bytes(output.read_bytes())
                info["file"] = stored_path.name
                stored.append(info)
            else:
                stored.append(self._invoke_prep(
                    task,
                    command,
                    tight_path,
                    output,
                    upscale="onnx",
                    size=3840,
                    expect_long_side=3840,
                    source_size=tight.size,
                    role="output",
                    already_cut=True,
                    require_object_side=3840,
                ))
        except DirectorRefusal as exc:
            task["results"] = stored
            if stored:
                task["result"] = stored[0]
            task["status"] = "failed"
            task["error"] = {"code": exc.code, "message": exc.message}
            return self.store.save(task)
        task["results"] = stored
        task["result"] = stored[-1]
        task["status"] = "completed"
        task["error"] = None
        task["fidelity"] = "cutout_plus_predicted_detail_not_label_identity"
        return self.store.save(task)

    def _invoke_prep(self, task, command, source, destination, *, upscale, size, expect_long_side, source_size, role, already_cut=False, require_object_side=None):
        argv = build_argv(command, source, destination, upscale=upscale, size=size, already_cut=already_cut)
        task.setdefault("provider_calls", []).append({"argv": argv, "role": role})
        self.store.save(task)
        code, stdout, stderr = run_prep_command(argv, self.prep_timeout)
        task["prep"].setdefault("runs", []).append({
            "role": role,
            "returncode": code,
            "stderr_tail": stderr[-500:],
        })
        if code != 0:
            raise DirectorRefusal("prep_failed", "prep_photo exited %s for %s and was not retried. %s" % (code, role, stderr[-300:]))
        if upscale == "onnx" and onnx_fallback(stderr):
            raise DirectorRefusal(
                "onnx_fallback",
                "prep_photo fell back off the requested ONNX Real-ESRGAN path. The output was not accepted.",
            )
        info = validate_png(destination, expect_long_side=expect_long_side, source_size=source_size)
        if require_object_side is not None:
            info["object"] = assert_object_long_side(destination, require_object_side)
        info["role"] = role
        info["media_type"] = "image/png"
        info["sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
        stored = self.results / ("%s-%s.png" % (task["id"], role))
        stored.write_bytes(destination.read_bytes())
        info["file"] = stored.name
        return info

    def _prepare_image(self, body):
        model_id = body.get("model") or "flux-2-pro"
        model = self._model(model_id, {"flux-2-pro", "venice-sd35"})
        prompt = self._prompt(body, (model.get("constraints") or {}).get("promptCharacterLimit") or 1500)
        params = {"model": model_id, "prompt": prompt, "fidelity": "generative_not_pixel_identity"}
        ratios = (model.get("constraints") or {}).get("aspectRatios") or []
        if ratios:
            aspect = body.get("aspect_ratio")
            if aspect not in ratios:
                raise DirectorRefusal("aspect_ratio_rejected", "aspect_ratio must be one of %s. An unlisted ratio is not sent, because Venice can silently fall back." % ratios)
            if body.get("width") or body.get("height"):
                raise DirectorRefusal("pixel_size_rejected", "This model is called with aspect_ratio. width and height were not sent.")
            params["aspect_ratio"] = aspect
        else:
            if body.get("aspect_ratio"):
                raise DirectorRefusal("aspect_ratio_unsupported", "venice-sd35 has no aspect-ratio list in the public catalog. aspect_ratio was not sent.")
            width = body.get("width", 1024)
            height = body.get("height", 1024)
            if [width, height] != [1024, 1024]:
                raise DirectorRefusal("pixel_size_rejected", "The documented example for venice-sd35 in this snapshot is 1024x1024. Other sizes were not sent.")
            params["width"] = 1024
            params["height"] = 1024
        if body.get("style_references"):
            raise DirectorRefusal("style_references_unsupported", "Style references are not enabled on this capability. They were not dropped silently.")
        return params

    def _prepare_edit(self, body):
        model_id = body.get("model") or "firered-image-edit"
        model = self._model(model_id, {"firered-image-edit"})
        if body.get("reference_asset_ids"):
            raise DirectorRefusal("extra_references_rejected", "image.edit takes one original. Use image.product_multiref for extra references. Extras were not dropped.")
        if body.get("resolution"):
            raise DirectorRefusal("resolution_unsupported", "firered-image-edit lists no resolutions. resolution was not sent.")
        prompt = self._prompt(body, (model.get("constraints") or {}).get("promptCharacterLimit") or 1500)
        aspect = body.get("aspect_ratio")
        ratios = (model.get("constraints") or {}).get("aspectRatios") or []
        if aspect is not None and aspect not in ratios:
            raise DirectorRefusal("aspect_ratio_rejected", "aspect_ratio is not in the live list for this edit model.")
        return {
            "model": model_id,
            "prompt": prompt,
            "original_asset_id": self._one_asset(body.get("original_asset_id")),
            "aspect_ratio": aspect,
            "fidelity": "generative_edit_not_pixel_identity",
        }

    def _prepare_product(self, body):
        model_id = body.get("model") or "firered-image-edit"
        model = self._model(model_id, {"firered-image-edit", "nano-banana-2-edit"})
        original = self._one_asset(body.get("original_asset_id"))
        references = list(body.get("reference_asset_ids") or [])
        if not references:
            raise DirectorRefusal("scene_reference_required", "The product multi-reference workflow needs the original plus at least one scene reference. A single photo belongs on image.edit.")
        cap = max_input_images(model)
        total = 1 + len(references)
        if total > cap:
            raise DirectorRefusal(
                "reference_cap_exceeded",
                "This model accepts %s input images. %s were provided. Nothing was dropped or moved to another model." % (cap, total),
            )
        if body.get("resolution"):
            allowed = (model.get("constraints") or {}).get("resolutions") or []
            if body["resolution"] not in allowed:
                raise DirectorRefusal("resolution_unsupported", "resolution is not in this model's live list, so it was not sent.")
        caller_prompt = self._prompt(body, 100000)
        prompt = caller_prompt if PRODUCT_GUARD.strip() in caller_prompt else caller_prompt.rstrip() + PRODUCT_GUARD
        limit = (model.get("constraints") or {}).get("promptCharacterLimit") or 1500
        if len(prompt) > limit:
            raise DirectorRefusal("prompt_too_long", "Prompt plus the product-fidelity sentence exceeds %s characters." % limit)
        aspect = body.get("aspect_ratio")
        ratios = (model.get("constraints") or {}).get("aspectRatios") or []
        if aspect is not None and aspect not in ratios:
            raise DirectorRefusal("aspect_ratio_rejected", "aspect_ratio is not in the live list for this model.")
        return {
            "model": model_id,
            "caller_prompt": caller_prompt,
            "prompt": prompt,
            "original_asset_id": original,
            "reference_asset_ids": references,
            "aspect_ratio": aspect,
            "resolution": body.get("resolution"),
            "input_cap": cap,
            "cap_basis": "maxInputImages from the public catalog, or 3 when that field is absent and combineImages is not false",
            "fidelity": "generative_edit_not_pixel_identity",
        }

    def _prepare_reference(self, body):
        model_id = body.get("model") or "seedance-2-5-reference-to-video-basic"
        model = self._model(model_id, {"seedance-2-5-reference-to-video-basic"})
        if body.get("camera_trajectory"):
            raise DirectorRefusal(
                "camera_mode_unsupported",
                "camera_trajectory is not part of seedance-2-5-reference-to-video-basic. It was not removed. Use video.venice_camera only for the Venice multi-angle route, or it will not be sent.",
            )
        if body.get("elements") or body.get("scene_image_urls"):
            raise DirectorRefusal("elements_unsupported", "Kling elements are not this model's route. They were not dropped.")
        constraints = model.get("constraints") or {}
        duration = body.get("duration")
        if duration not in (constraints.get("durations") or []):
            raise DirectorRefusal("duration_rejected", "duration must be one of the live catalog values %s." % constraints.get("durations"))
        aspect = body.get("aspect_ratio")
        if aspect not in (constraints.get("aspect_ratios") or []):
            raise DirectorRefusal("aspect_ratio_rejected", "aspect_ratio must be one of the live catalog values. adaptive/auto are not in that list.")
        resolution = body.get("resolution")
        if resolution is not None and resolution not in (constraints.get("resolutions") or []):
            raise DirectorRefusal("resolution_rejected", "resolution is not in the live list.")
        references = list(body.get("reference_asset_ids") or [])
        image_asset = body.get("original_asset_id")
        if not references and not image_asset:
            raise DirectorRefusal("reference_required", "The reference workflow needs a start frame or reference images. It was not downgraded to text-to-video.")
        caps = self.catalog["reference_caps_not_in_live_constraints"][model_id]
        if len(references) > caps["images"]:
            raise DirectorRefusal("reference_cap_exceeded", "Reference images exceed the skill/OpenAPI ceiling of %s." % caps["images"])
        if body.get("audio") is not None and not constraints.get("audio_configurable"):
            raise DirectorRefusal("audio_flag_unsupported", "audio was not sent because audio_configurable is false.")
        prompt = self._prompt(body, constraints.get("prompt_character_limit") or 2500)
        return {
            "model": model_id,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect,
            "resolution": resolution,
            "image_asset_id": image_asset,
            "reference_asset_ids": references,
            "reference_cap_basis": caps["basis"],
            "fidelity": "generative_not_pixel_identity",
        }

    def _prepare_camera(self, body):
        model_id = "minimax-h3-max-multi-angle"
        if body.get("model") not in (None, model_id):
            raise DirectorRefusal("model_not_this_lane", "video.venice_camera runs only as Venice model minimax-h3-max-multi-angle. It was not retargeted.")
        model = self._model(model_id, {model_id})
        if body.get("aspect_ratio"):
            raise DirectorRefusal("aspect_ratio_rejected", "The live catalog lists no aspect ratios for this model. aspect_ratio was not sent.")
        trajectory = body.get("camera_trajectory")
        if not isinstance(trajectory, list) or not 2 <= len(trajectory) <= 12:
            raise DirectorRefusal("camera_trajectory_required", "This lane requires 2 to 12 camera_trajectory keyframes. The field was not invented or dropped.")
        for point in trajectory:
            if not isinstance(point, dict) or not {"time", "azimuth", "elevation", "distance"} <= set(point):
                raise DirectorRefusal("camera_trajectory_invalid", "Each keyframe needs time, azimuth, elevation and distance.")
        constraints = model.get("constraints") or {}
        duration = body.get("duration")
        if duration not in (constraints.get("durations") or []):
            raise DirectorRefusal("duration_rejected", "duration is not in the live list.")
        resolution = body.get("resolution") or "768P"
        if resolution not in (constraints.get("resolutions") or []):
            raise DirectorRefusal("resolution_rejected", "resolution is not in the live list %s." % constraints.get("resolutions"))
        if body.get("reference_asset_ids"):
            raise DirectorRefusal("references_rejected", "Reference images are a different route. They were not mixed into the camera request.")
        return {
            "model": model_id,
            "prompt": body.get("prompt") or "Camera orbit",
            "duration": duration,
            "resolution": resolution,
            "image_asset_id": self._one_asset(body.get("original_asset_id")),
            "camera_trajectory": trajectory,
            "lane_note": "Venice-hosted multi-angle route. Not the original MiniMax API, not a local Max checkpoint, and not Base weights.",
            "fidelity": "generative_not_pixel_identity",
        }

    def _prepare_speech(self, body):
        model = self._model("tts-kokoro", {"tts-kokoro"})
        if body.get("model") not in (None, "tts-kokoro"):
            raise DirectorRefusal("model_not_this_lane", "This speech capability uses tts-kokoro only.")
        voice = body.get("voice") or "af_sky"
        if voice not in (model.get("voices") or []):
            raise DirectorRefusal("voice_rejected", "Voice is not in the public tts-kokoro voice list.")
        if body.get("sample_url") or body.get("clone"):
            raise DirectorRefusal("voice_clone_rejected", "Voice cloning is not part of this capability.")
        text = body.get("narration") or body.get("prompt") or body.get("input")
        if not isinstance(text, str) or not text.strip():
            raise DirectorRefusal("text_required", "Narration text is required.")
        if len(text) > 4096:
            raise DirectorRefusal("text_too_long", "Speech input is limited to 4096 characters by the speech API.")
        return {"model": "tts-kokoro", "voice": voice, "text": text}

    def _prepare_explainer(self, body):
        video_model = body.get("video_model") or body.get("model") or "wan-2.5-preview-text-to-video"
        model = self._model(video_model, {"wan-2.5-preview-text-to-video"})
        constraints = model.get("constraints") or {}
        aspect = body.get("aspect_ratio")
        if aspect not in (constraints.get("aspect_ratios") or []):
            raise DirectorRefusal("aspect_ratio_rejected", "Explainer clips use a live aspect ratio of the text-to-video model.")
        resolution = body.get("resolution") or "720p"
        if resolution not in (constraints.get("resolutions") or []):
            raise DirectorRefusal("resolution_rejected", "resolution is not in the live list.")
        scenes_in = body.get("scenes")
        if not isinstance(scenes_in, list) or not scenes_in:
            raise DirectorRefusal("scenes_required", "Explainer needs at least one scene. A chat model was not called to invent one.")
        speech = self._prepare_speech({"voice": body.get("voice"), "narration": "ok"})
        scenes = []
        for index, scene in enumerate(scenes_in):
            if not isinstance(scene, dict):
                raise DirectorRefusal("scene_invalid", "Each scene must be an object.")
            duration = scene.get("duration")
            if duration not in (constraints.get("durations") or []):
                raise DirectorRefusal("duration_rejected", "Scene duration must be one of %s." % constraints.get("durations"))
            prompt = scene.get("prompt")
            narration = scene.get("narration")
            if not isinstance(prompt, str) or not prompt.strip() or not isinstance(narration, str) or not narration.strip():
                raise DirectorRefusal("scene_incomplete", "Each scene needs a prompt and narration.")
            if len(narration) > 4096:
                raise DirectorRefusal("text_too_long", "Scene narration exceeds 4096 characters.")
            scenes.append({"index": index, "prompt": prompt.strip(), "narration": narration.strip(), "duration": duration})
        return {
            "model": video_model,
            "video_model": video_model,
            "speech_model": "tts-kokoro",
            "voice": body.get("voice") or speech["voice"],
            "aspect_ratio": aspect,
            "resolution": resolution,
            "style_line": (body.get("style_line") or "Consistent explainer look.").strip(),
            "scenes": scenes,
            "music": "not_wired",
            "music_reason": "Async music queue OpenAPI was not re-fetched, so a music bed is not sent.",
        }

    def _prepare_max(self, body):
        if body.get("model") not in (None, "MiniMax-H3-Max"):
            raise DirectorRefusal(
                "model_not_this_lane",
                "video.h3_max_hosted sends model MiniMax-H3-Max only. MiniMax-H3, Base weights, Comfy and Venice ids were not substituted.",
            )
        if body.get("camera_trajectory"):
            raise DirectorRefusal(
                "camera_mode_unsupported",
                "The original MiniMax video schema has no camera_trajectory. It was not dropped and not routed to Venice or fal.",
            )
        resolution = body.get("resolution")
        if resolution == "2K":
            raise DirectorRefusal(
                "resolution_not_supported_no_fallback",
                "MiniMax-H3-Max does not support 2K. The request was not rewritten to 768P, and Regenerate-2K was not called.",
            )
        if resolution not in ("480P", "768P"):
            raise DirectorRefusal("resolution_rejected", "MiniMax-H3-Max resolution must be 480P or 768P.")
        duration = body.get("duration")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration not in range(5, 16):
            raise DirectorRefusal("duration_rejected", "MiniMax-H3-Max duration is an integer from 5 to 15. 4 seconds and Venice-style strings are rejected.")
        mode_field = body.get("prompt_expansion_mode")
        if mode_field not in ("disabled", "balanced", "quality"):
            raise DirectorRefusal("prompt_expansion_mode_required", "Set prompt_expansion_mode to disabled, balanced or quality. The server default was not assumed.")
        prompt = self._prompt(body, 7000)
        original = body.get("original_asset_id")
        references = list(body.get("reference_asset_ids") or [])
        if original and references:
            raise DirectorRefusal("mixed_frame_and_reference", "First-frame and reference images are mutually exclusive on this API. They were not mixed.")
        if references:
            if len(references) > 9:
                raise DirectorRefusal("reference_cap_exceeded", "MiniMax reference images are capped at 9. Extras were not dropped.")
            mode = "reference"
            ratio = body.get("ratio") or body.get("aspect_ratio") or "adaptive"
            allowed = ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
            if ratio not in allowed:
                raise DirectorRefusal("ratio_rejected", "ratio is not in the MiniMax enum.")
        elif original:
            mode = "first_frame"
            ratio = body.get("ratio") or body.get("aspect_ratio")
            if ratio != "adaptive":
                raise DirectorRefusal(
                    "ratio_must_be_adaptive",
                    "For a first frame the original API ignores any ratio other than adaptive. A different ratio was not sent.",
                )
        else:
            mode = "text"
            ratio = body.get("ratio") or body.get("aspect_ratio")
            if ratio not in ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"):
                raise DirectorRefusal("ratio_required", "Text-to-video requires a concrete ratio. adaptive was not sent.")
        return {
            "model": "MiniMax-H3-Max",
            "prompt": prompt,
            "resolution": resolution,
            "duration": duration,
            "ratio": ratio,
            "mode": mode,
            "original_asset_id": original,
            "reference_asset_ids": references,
            "prompt_expansion_mode": mode_field,
            "lane": "hosted_max",
            "fidelity": "generative_not_pixel_identity",
        }

    def _model(self, model_id, allowed):
        if model_id not in allowed:
            raise DirectorRefusal("model_not_allowlisted", "Model %s is not allowlisted for this capability. No substitute was chosen." % model_id)
        model = self.models.get(model_id)
        if not model:
            raise DirectorRefusal("model_not_in_snapshot", "Model is not in the curated public snapshot.")
        if model.get("offline"):
            raise DirectorRefusal("model_offline", "The public catalog marks this model offline.")
        return model

    def _prompt(self, body, limit):
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise DirectorRefusal("prompt_required", "prompt is required", 400)
        if len(prompt) > limit:
            raise DirectorRefusal("prompt_too_long", "Prompt exceeds %s characters." % limit)
        return prompt.strip()

    def _one_asset(self, asset_id):
        if not isinstance(asset_id, str) or not asset_id:
            raise DirectorRefusal("original_required", "original_asset_id is required")
        if self.assets.get(asset_id) is None:
            raise DirectorRefusal("asset_not_found", "Original asset is not in the local store", 404)
        return asset_id

    def _require_asset(self, asset_id):
        asset = self.assets.get(asset_id)
        if asset is None:
            raise DirectorRefusal("asset_not_found", "Asset disappeared before the provider call. Nothing was sent.", 404)
        return asset

    def _reject_identity(self, body):
        mode = body.get("identity_mode") or body.get("face_mode")
        if mode in BLOCKED_IDENTITY:
            raise DirectorRefusal(
                "identity_mode_unsupported",
                "Pixel-identical face restore is not a mode of these generative tools. The request was not rewritten into an edit.",
            )

    def _need_key(self, provider):
        if provider == "venice" and not self.venice_api_key:
            raise DirectorRefusal("credential_missing", "VENICE_API_KEY is not set on the server. Nothing was sent.")
        if provider == "minimax" and not self.minimax_api_key:
            raise DirectorRefusal("credential_missing", "MINIMAX_API_KEY is not set on the server. Nothing was sent.")

    def _blank(self, body, spec):
        return {
            "id": str(uuid.uuid4()),
            "client_request_id": body["client_request_id"].strip(),
            "capability": body.get("capability") or spec.get("id"),
            "provider": spec.get("provider"),
            "status": "accepted",
            "confirm": bool(body.get("confirm")),
            "created_at": _now(),
            "model": None,
            "params": None,
            "provider_task_id": None,
            "poll_count": 0,
            "max_polls": MAX_POLLS,
            "provider_calls": [],
            "error": None,
            "fidelity": "generative_not_pixel_identity",
        }

    def _transport_failed(self, task, exc):
        task["status"] = "submit_uncertain" if exc.uncertain else "failed"
        task["error"] = {
            "code": task["status"],
            "message": str(exc) + " The request was not retried.",
        }
        return self.store.save(task)

    def _attach_bytes(self, task, data, media_type):
        item = self._write_result(task["id"], data, media_type)
        task["result"] = item
        task["results"] = [item]

    def _write_result(self, name, data, media_type):
        path = self.results / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return {
            "file": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "media_type": media_type,
            "mocked_provider_bytes": False,
        }

    def _spec(self, capability_id):
        for spec in self._specs():
            if spec["id"] == capability_id:
                return spec
        return None

    def _spec_or_lane(self, capability_id):
        spec = self._spec(capability_id)
        if spec:
            return spec
        for lane in static_lanes():
            if lane["id"] == capability_id:
                return lane
        report = self.comfy_by_id.get(capability_id)
        if report:
            return {"id": report["id"], "provider": "comfy", "kind": "comfy", **report}
        return None

    def _specs(self):
        max_spec = hosted_max_spec()
        return [
            {"id": "image.create", "title": "Создать изображение", "provider": "venice", "kind": "image_generate", "skills": ["venice-create-image", "venice-models"], "endpoint": "POST /api/v1/image/generate", "docs": "https://docs.venice.ai/api-reference/endpoint/image/generate", "models": ["flux-2-pro", "venice-sd35"], "default_model": "flux-2-pro"},
            {"id": "image.edit", "title": "Править одно изображение", "provider": "venice", "kind": "image_edit", "skills": ["venice-image-edit"], "endpoint": "POST /api/v1/image/edit", "docs": "https://docs.venice.ai/api-reference/endpoint/image/edit", "models": ["firered-image-edit"], "default_model": "firered-image-edit"},
            {"id": "image.product_multiref", "title": "Товар: оригинал и референсы сцены", "provider": "venice", "kind": "image_product", "skills": ["venice-product-photoshoot", "venice-image-edit"], "endpoint": "POST /api/v1/image/multi-edit", "docs": "https://docs.venice.ai/api-reference/endpoint/image/multi-edit", "models": ["firered-image-edit", "nano-banana-2-edit"], "default_model": "firered-image-edit"},
            {"id": "video.reference", "title": "Видео по референсам", "provider": "venice", "kind": "video_reference", "skills": ["venice-create-video", "venice-video"], "endpoint": "POST /api/v1/video/queue", "docs": "https://docs.venice.ai/api-reference/endpoint/video/queue", "models": ["seedance-2-5-reference-to-video-basic"], "default_model": "seedance-2-5-reference-to-video-basic"},
            {"id": "video.venice_camera", "title": "Камера Venice H3 Max multi-angle", "provider": "venice", "kind": "video_venice_camera", "skills": ["venice-video"], "endpoint": "POST /api/v1/video/queue", "docs": "https://docs.venice.ai/api-reference/endpoint/video/queue", "models": ["minimax-h3-max-multi-angle"], "default_model": "minimax-h3-max-multi-angle"},
            {"id": "audio.speech", "title": "Речь", "provider": "venice", "kind": "audio_speech", "skills": ["venice-create-audio"], "endpoint": "POST /api/v1/audio/speech", "docs": "https://docs.venice.ai/api-reference/endpoint/audio/speech", "models": ["tts-kokoro"], "default_model": "tts-kokoro"},
            {"id": "explainer.project", "title": "Объясняющее видео: сцены и речь", "provider": "venice", "kind": "explainer", "skills": ["venice-explainer-video", "venice-create-video", "venice-create-audio"], "endpoint": "POST /api/v1/video/queue", "docs": "https://docs.venice.ai/guides/media/video-generation", "models": ["wan-2.5-preview-text-to-video"], "default_model": "wan-2.5-preview-text-to-video"},
            max_spec,
            {
                "id": "image.product_prep",
                "title": "Товар: вырез фона и длинная сторона 3840",
                "provider": "local_prep",
                "kind": "product_prep",
                "skills": [],
                "endpoint": "prep_photo --model u2net --bg transparent --format png --size 3840 --upscale onnx --denoise none",
                "docs": "director/vendor/photo-prep/README.md",
                "default_model": "u2net",
                "models": [],
            },
            *[{
                "id": item["id"],
                "title": item["title"],
                "provider": "ltx_local",
                "kind": "ltx",
                "module": item["module"],
                "temporal_edit": item["temporal_edit"],
                "summary": item["summary"],
                "skills": [],
                "endpoint": "python -m " + item["module"],
                "docs": ltx_route.REPO,
                "models": [],
            } for item in ltx_route.FAMILIES],
            *[{
                "id": item["id"],
                "title": item["title"],
                "provider": "imagination_studio",
                "kind": "studio",
                "endpoint": item["endpoint"],
                "docs": studio_route.REPO,
                "summary": item["summary"],
                "submits": item["submits"],
                "skills": [],
                "models": [],
            } for item in studio_route.FAMILIES],
        ]

    def _public_capability(self, spec):
        problems = skills_ready(self.skills, spec.get("skills") or []) if spec.get("provider") == "venice" else []
        key_ok = (spec.get("provider") != "venice" or bool(self.venice_api_key)) and (spec.get("provider") != "minimax" or bool(self.minimax_api_key))
        if spec.get("provider") == "local_prep":
            status = "ready_to_confirm" if resolve_command(self.prep_photo_command) else "prep_command_missing"
        elif spec.get("provider") == "ltx_local":
            report = self._ltx_probe()
            status = "ready_to_confirm" if report["ready"] else "ltx_not_ready"
        elif spec.get("provider") == "imagination_studio":
            status = studio_route.readiness(spec["id"], self._studio_probe())
        elif problems:
            status = "skill_integrity_failed"
        elif not key_ok:
            status = "credential_missing"
        else:
            status = "ready_to_confirm"
        models = []
        for model_id in spec.get("models") or ([spec["model"]] if spec.get("model") else []):
            model = self.models.get(model_id)
            if model:
                models.append({
                    "id": model_id,
                    "name": model.get("name"),
                    "constraints": model.get("constraints"),
                    "offline": model.get("offline"),
                })
        return {
            "id": spec["id"],
            "title": spec.get("title") or spec["id"],
            "provider": spec.get("provider"),
            "kind": spec.get("kind"),
            "lane": spec.get("lane"),
            "endpoint": spec.get("endpoint"),
            "docs": spec.get("docs"),
            "default_model": spec.get("default_model") or spec.get("model"),
            "models": models,
            "skills": [self.skills[name] for name in spec.get("skills") or [] if name in self.skills],
            "readiness": {"status": status, "credential": "present" if key_ok else "missing"},
            "pixel_identity": False,
            "origin": spec.get("origin"),
            "resolutions": spec.get("resolutions"),
            "resolutions_rejected": spec.get("resolutions_rejected"),
            "camera_trajectory": spec.get("camera_trajectory", spec["id"] == "video.venice_camera"),
            "commercial_runtime": spec.get("commercial_runtime"),
            "temporal_edit": spec.get("temporal_edit"),
            "summary": spec.get("summary"),
            "ltx": self._ltx_probe() if spec.get("provider") == "ltx_local" else None,
            "studio": studio_route.brief(self._studio_probe()) if spec.get("provider") == "imagination_studio" else None,
        }

    def _public_lane(self, lane):
        return {
            "id": lane["id"],
            "title": lane["title"],
            "provider": "none",
            "kind": "h3_refused",
            "lane": lane["lane"],
            "endpoint": lane.get("endpoint"),
            "docs": lane.get("docs") or lane.get("hub"),
            "readiness": {"status": lane["run_status"], "credential": "not_applicable"},
            "published_weights": lane.get("published_weights"),
            "downloadable": lane.get("downloadable", False),
            "weights_downloaded_by_director": False,
            "commercial_runtime": "disabled",
            "reason": lane.get("reason"),
            "not_the_same_as": lane.get("not_the_same_as"),
            "pixel_identity": False,
            "runner": lane.get("runner"),
            "documented_revision": lane.get("documented_revision"),
        }

    def _public_comfy(self, report):
        ready = "ready_to_stage" if report["sha256_ok"] and report["expects"]["unet_present"] else "integrity_failed"
        return {
            "id": report["id"],
            "title": report["title"],
            "provider": "comfy",
            "kind": "comfy",
            "lane": report["lane"],
            "readiness": {"status": "external_runner_required", "integrity": ready},
            "commercial_runtime": "disabled",
            "json_license": report["json_license"],
            "weight_filenames": report["weight_filenames"],
            "labels": report["labels"],
            "reason": report["commercial_reason"],
            "installed": False,
            "pixel_identity": False,
        }


def _error_text(result, body):
    if isinstance(body, dict):
        if body.get("error"):
            return str(body["error"])[:500]
        return str(body)[:500]
    text = result.body[:300].decode("utf-8", errors="replace") if result.body else ""
    return "HTTP %s %s" % (result.status, text)
