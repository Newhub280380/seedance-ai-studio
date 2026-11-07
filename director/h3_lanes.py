"""H3 lanes kept separate. None of these call a provider by being listed."""

LICENSE_URL = "https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE"
AUDIT_DATE = "2026-11-07"

COMMERCIAL = {
    "commercial_runtime": "disabled",
    "json_mit_does_not_enable": True,
    "license_name": "MiniMax H3 Community License",
    "license_url": LICENSE_URL,
    "audit_date": AUDIT_DATE,
    "restrictions": [
        "Excluded territories include the EU, UK, Republic of Korea and the USA; the exclusion covers works and outputs, not only GPU location.",
        "Yearly revenue of commercial products or services above USD 20M needs prior written authorization.",
        "A commercial product UI must prominently display MiniMax H3.",
        "The embedded Qwen3-VL-32B encoder being Apache-2.0 does not relicense H3.",
        "Hosted MiniMax API availability is not a license to self-host the same weights worldwide.",
    ],
}


def static_lanes():
    common = {
        "provider": "none",
        "kind": "h3_refused",
        "commercial_runtime": "disabled",
        "pixel_identity": False,
        "weights_downloaded_by_director": False,
    }
    return [
        {
            **common,
            "id": "h3.base_fl2va_original",
            "title": "H3 Base FL2VA — оригинальные веса",
            "lane": "base_original",
            "family": "FL2VA",
            "published_weights": True,
            "weight_form": "CFG-distilled Omni Transformer shards, mixed BF16/FP32 per the deployment guide",
            "hub": "https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/FL2VA",
            "runner": "https://platform.minimax.io/docs/guides/local-deploy-h3",
            "documented_revision": "42ed227ee7df40d41602854ae760620d6eb651fe",
            "documented_revision_note": "Revision pinned by the official self-host guide. Not asserted as today's HEAD.",
            "native_output": "768p video and stereo audio",
            "run_status": "weights_published_runner_not_deployed",
            "not_the_same_as": ["h3.max_hosted", "h3.context_ir_hosted", "h3.regenerate_2k_hosted", "comfy.h3_base_fl2va_quant_turbo"],
        },
        {
            **common,
            "id": "h3.base_ref2va_original",
            "title": "H3 Base Ref2VA — оригинальные веса",
            "lane": "base_original",
            "family": "Ref2VA",
            "published_weights": True,
            "weight_form": "Separate Ref2VA shard set, not the FL2VA shards renamed",
            "hub": "https://huggingface.co/MiniMaxAI/MiniMax-H3/tree/main/Ref2VA",
            "runner": "https://platform.minimax.io/docs/guides/local-deploy-h3",
            "documented_revision": "42ed227ee7df40d41602854ae760620d6eb651fe",
            "documented_revision_note": "Revision pinned by the official self-host guide. Not asserted as today's HEAD.",
            "native_output": "768p video and stereo audio",
            "run_status": "weights_published_runner_not_deployed",
            "not_the_same_as": ["h3.max_hosted", "h3.context_ir_hosted", "h3.regenerate_2k_hosted", "comfy.h3_base_ref2va_quant_turbo"],
        },
        {
            **common,
            "id": "h3.context_ir_hosted",
            "title": "H3 Context-IR — только hosted",
            "lane": "hosted_closed",
            "published_weights": False,
            "downloadable": False,
            "endpoint": "POST https://api.minimax.io/v2/h3_context_ir",
            "docs": "https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir",
            "run_status": "hosted_component_not_published",
            "reason": "The author excludes Context-IR from the open-weight release and documents a hosted API. This runtime does not call that API and does not replace it with Base or Comfy.",
        },
        {
            **common,
            "id": "h3.regenerate_2k_hosted",
            "title": "H3 Regenerate-2K — только hosted",
            "lane": "hosted_closed",
            "published_weights": False,
            "downloadable": False,
            "endpoint": "POST https://api.minimax.io/v2/video_regeneration",
            "docs": "https://platform.minimax.io/docs/api-reference/video-generation-v2-regeneration",
            "not_pixel_upscale": True,
            "run_status": "hosted_component_not_published",
            "reason": "Regenerate-2K is a hosted generative recreation from original context plus a 768p video. It is not a published checkpoint and not a pixel-preserving upscale. This runtime does not call it and does not substitute Base or Comfy.",
        },
    ]


def hosted_max_spec():
    return {
        "id": "video.h3_max_hosted",
        "title": "H3 Max — hosted MiniMax API",
        "provider": "minimax",
        "kind": "h3_max_hosted",
        "lane": "hosted_max",
        "model": "MiniMax-H3-Max",
        "endpoint": "POST https://api.minimax.io/v2/video_generation",
        "query_endpoint": "GET https://api.minimax.io/v2/query/video_generation/{task_id}",
        "docs": "https://platform.minimax.io/docs/api-reference/video-generation-v2-create",
        "origin": "Post-trained by fal on open H3. Not a runtime switch on Base weights. No separate public Max checkpoint was verified.",
        "resolutions": ["480P", "768P"],
        "resolutions_rejected": ["2K"],
        "duration_seconds": list(range(5, 16)),
        "ratios_text": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
        "reference_image_cap": 9,
        "reference_video_cap": 3,
        "reference_audio_cap": 3,
        "camera_trajectory": False,
        "prompt_expansion_modes": ["disabled", "balanced", "quality"],
        "credential_env": "MINIMAX_API_KEY",
        "commercial_runtime": "api_terms_not_self_host_license",
        "pixel_identity": False,
        "skills": [],
    }
