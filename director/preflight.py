"""Dry preflight. GET only. No job, no weight download, no GPU rental, no portrait inference."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from director.assets import POST_SCHEMA
from director.prep_route import resolve_command
from director.service import DirectorApp, _brain_public
from director.studio_route import (
    BEAUTY_RETOUCH_PROMPT_EXAMPLE,
    CLASSIC_SECONDS,
    IMAGE_EDIT_MODELS,
    PIN,
    PRODUCTION_IDENTITY,
    REPO,
    VERSION,
    brief,
)

EXAMPLE_PROMPT = BEAUTY_RETOUCH_PROMPT_EXAMPLE


def collect(repo_root: Path, data_dir: Path, transport=None) -> dict:
    app = DirectorApp(repo_root, data_dir, _UnusedTransport(), _UnusedTransport())
    if transport is not None:
        app.studio_transport = transport
    info = brief(app._studio_probe())
    onnx = os.environ.get("PREP_ONNX_MODEL") or ""
    return {
        "dry_preflight": True,
        "gpu_tested": False,
        "paid_api_called": False,
        "weights_downloaded": False,
        "jobs_submitted": False,
        "production_identity": PRODUCTION_IDENTITY,
        "assets": POST_SCHEMA,
        "prep": {
            "command_configured": app.prep_photo_command,
            "command_resolved": resolve_command(app.prep_photo_command),
            "onnx_model": onnx,
            "onnx_model_file_present": bool(onnx) and Path(onnx).is_file(),
            "neural_weights_executed": False,
            "beauty_retouch": False,
            "realesrgan_is_beauty_retouch": False,
            "subjects": ["person", "object", "generic"],
            "mask_model": "u2net",
            "note": "File presence is not an ONNX session. u2net is the same salient mask for a person or an object. It is not a face restorer.",
        },
        "studio": info,
        "routes": {
            "image_subject_prep": {
                "capability": "image.subject_prep",
                "generative": False,
                "requires_h3": False,
                "deps": [
                    "rembg CLI with the u2net weights it already uses",
                    "PREP_ONNX_MODEL pointing at an existing Real-ESRGAN x4plus ONNX file",
                    "onnxruntime and numpy, imported by prep_photo only when --upscale onnx runs",
                    "Pillow, already required by Director, for the white JPEG derivative",
                ],
            },
            "studio_image_edit": {
                "capability": "studio.image_edit",
                "requires_h3": False,
                "default_model": "qwen",
                "allowlist": list(IMAGE_EDIT_MODELS),
                "pin": PIN,
                "version": VERSION,
                "repo": REPO,
                "script": "scripts/edit.sh",
                "http": [
                    "GET /api/edit/state",
                    "POST /api/upload multipart field file",
                    "POST /api/jobs {kind: edit, params: {image, prompt, image_model, seed}}",
                    "GET /api/jobs/{id}",
                    "GET /files/{path}",
                ],
                "weights": {
                    "qwen": "Qwen-Image-2.1-Turbo. On the Spark/CUDA tree: Qwen-Image-2.1-Comfy diffusion_models/qwen_image_2.1_turbo_bf16.safetensors, text encoder and vae. On a Mac: Qwen-Image-2.1-Turbo with processor and vae. This is the picture install, not --ref2va and not H3.",
                    "qwen-full": "Optional Qwen-Image-2.1 40-step base via oneshot-setup.sh --image-edit.",
                    "flux3k-fast": "Optional FLUX3-K Fast via oneshot-setup.sh --flux3.",
                    "flux3k-full": "Optional FLUX3-K Full via oneshot-setup.sh --flux3.",
                    "moss-image": "Optional MOSS-Image via oneshot-setup.sh --moss.",
                },
                "not_required": ["MiniMax H3", "Ref2VA", "FastH3", "Real-ESRGAN"],
                "prompt_default": None,
                "beauty_retouch_applied_by_default": False,
            },
            "studio_reference_video": {
                "capability": "studio.reference_video",
                "optional": True,
                "requires_h3": True,
                "seconds": list(CLASSIC_SECONDS),
                "weights": "MiniMax H3 Ref2VA transformer beside the text encoder and decoders. Mac: oneshot-setup.sh --ref2va. Spark/Linux: oneshot-setup-spark.sh --ref2va. Not used for image edit.",
            },
            "studio_swap": {
                "capability": "studio.swap",
                "optional": True,
                "requires_h3": True,
                "note": "Character swap is not the primary path.",
            },
        },
        "plan": {
            "capability": "director.plan",
            "preflight_calls_brain": False,
            "duration_seconds": 120,
            "aspect_ratio": "9:16",
            "render_started": False,
            "brain": _brain_public(app.brain_url, app.brain_model),
            "note": "Set DIRECTOR_BRAIN_URL and DIRECTOR_BRAIN_MODEL before a plan task. Preflight does not call the brain.",
        },
        "examples": _examples(),
    }


def _examples() -> dict:
    prompt = EXAMPLE_PROMPT
    prep = {
        "capability": "image.subject_prep",
        "client_request_id": "portrait-prep-1",
        "confirm": True,
        "subject": "person",
        "original_asset_id": "<sha256 of the operator's own portrait>",
    }
    edit = {
        "capability": "studio.image_edit",
        "client_request_id": "portrait-edit-1",
        "confirm": True,
        "prep_task_id": "<id returned by image.subject_prep>",
        "reference_role": "jpeg_white",
        "image_model": "qwen",
        "prompt": prompt,
    }
    return {
        "identity": "Replace the placeholders with the operator's own portrait. No portrait is bundled. Nothing here is submitted by preflight.",
        "comfy_sample": "not_a_payload",
        "asset_post": {
            "content_type": "application/json",
            "body": {"media_type": "image/jpeg", "content_base64": "<standard base64 of the portrait bytes>"},
        },
        "subject_prep": prep,
        "image_edit": edit,
        "plan": {
            "capability": "director.plan",
            "client_request_id": "skate-plan-1",
            "confirm": True,
            "brief": "<operator brief. Private photos stay on the box; this text names no file bytes.>",
            "duration_seconds": 120,
            "aspect_ratio": "9:16",
            "references": [
                {"asset_id": "<sha256>", "role": "cast", "label": "father"},
                {"asset_id": "<sha256>", "role": "cast", "label": "daughter"},
                {"asset_id": "<sha256>", "role": "place", "label": "cafe"},
                {"asset_id": "<sha256>", "role": "wardrobe", "label": "daughter_full_body"},
                {"asset_id": "<sha256>", "role": "place", "label": "rink"},
                {"asset_id": "<sha256>", "role": "style_motion", "label": "style_reference"},
            ],
        },
        "reference_video_smoke_seconds": 5,
        "reference_video_optional": {
            "capability": "studio.reference_video",
            "client_request_id": "portrait-ref-video-1",
            "confirm": True,
            "prep_task_id": "<id returned by image.subject_prep>",
            "reference_role": "jpeg_white",
            "seconds": 5,
            "prompt": "Use <Picture 1> as the person. The person in <Picture 1> turns their head slightly.",
        },
        "commands": [
            "python -m director.preflight",
            "python -m director",
            "curl -sS -X POST http://127.0.0.1:8765/api/director/assets -H 'Content-Type: application/json' -d '{\"media_type\":\"image/jpeg\",\"content_base64\":\"<base64>\"}'",
            "curl -sS -X POST http://127.0.0.1:8765/api/director/tasks -H 'Content-Type: application/json' -d '<subject_prep json>'",
            "curl -sS -X POST http://127.0.0.1:8765/api/director/tasks -H 'Content-Type: application/json' -d '<studio.image_edit json>'",
            "curl -sS -X POST http://127.0.0.1:8765/api/director/tasks/<edit-id>/poll -H 'Content-Type: application/json' -d '{}'",
            "curl -sS -D - http://127.0.0.1:8765/api/director/tasks/<prep-id>/result?role=output -o master.png",
            "curl -sS -D - http://127.0.0.1:8765/api/director/tasks/<prep-id>/result?role=jpeg_white -o white.jpg",
        ],
    }


class _UnusedTransport:
    def request(self, method, url, headers, body, timeout):
        raise AssertionError("preflight must not call a paid provider")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    configured = os.environ.get("DIRECTOR_DATA_DIR")
    if configured:
        data = Path(configured)
        report = collect(root, data)
    else:
        with tempfile.TemporaryDirectory(prefix="director-preflight-") as temporary:
            report = collect(root, Path(temporary))
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
