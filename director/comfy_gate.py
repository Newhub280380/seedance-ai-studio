import hashlib
import json
import re
from pathlib import Path


TEMPLATES = (
    {
        "id": "comfy.h3_base_ref2va_quant_turbo",
        "title": "Comfy: H3 Base Ref2VA, pruned INT8 + Turbo LoRA",
        "file": "comfy-h3/video_minimax_h3_r2v.json",
        "lane": "comfy_converted_base_turbo",
        "family": "Ref2VA",
        "expect_unet": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "expect_lora": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        "expect_node": "MiniMaxH3ReferenceToVideo",
        "license_file": "comfy-h3/LICENSE",
        "json_license": "MIT",
        "commercial_block": "h3",
    },
    {
        "id": "comfy.h3_base_fl2va_quant_turbo",
        "title": "Comfy: H3 Base FL2VA, pruned INT8 + Turbo LoRA",
        "file": "comfy-h3/video_minimax_h3_t2v.json",
        "lane": "comfy_converted_base_turbo",
        "family": "FL2VA",
        "expect_unet": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "expect_lora": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
        "expect_node": "MiniMaxH3ImageToVideo",
        "license_file": "comfy-h3/LICENSE",
        "json_license": "MIT",
        "commercial_block": "h3",
    },
    {
        "id": "comfy.firered_edit_1_0",
        "title": "Comfy: FireRed Image Edit 1.0",
        "file": "firered-comfy/firered-image-edit-1.0.json",
        "lane": "comfy_firered_1_0",
        "family": "FireRed-Image-Edit-1.0",
        "expect_unet": "FireRed-Image-Edit-1.0-transformer.safetensors",
        "expect_lora": "FireRed-Image-Edit-1.0-Lightning-8steps-v1.1.safetensors",
        "expect_node": "UNETLoader",
        "license_file": "firered-comfy/LICENSE-2.0.txt",
        "json_license": "Apache-2.0",
        "commercial_block": "firered",
    },
)

CLOSED_NAME_MARKERS = ("context-ir", "context_ir", "regenerate2k", "regenerate-2k", "regenerate_2k")


def inspect_templates(vendor_root):
    vendor_root = Path(vendor_root)
    manifest = {item["file"]: item for item in json.loads((vendor_root / "manifest.json").read_text(encoding="utf-8"))}
    reports = []
    for template in TEMPLATES:
        path = vendor_root / template["file"]
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        graph = json.loads(raw.decode("utf-8"))
        nodes = [node for node in graph.get("nodes") or [] if isinstance(node, dict)]
        types = sorted({node.get("type") for node in nodes if node.get("type")})
        names = sorted(set(re.findall(r"[\w.\-]+\.safetensors", raw.decode("utf-8"))))
        closed = [name for name in names if any(marker in name.lower() for marker in CLOSED_NAME_MARKERS)]
        record = manifest[template["file"]]
        reports.append({
            "id": template["id"],
            "title": template["title"],
            "lane": template["lane"],
            "family": template["family"],
            "file": template["file"],
            "sha256": digest,
            "sha256_ok": digest == record["sha256"],
            "source": record.get("source"),
            "json_license": template["json_license"],
            "license_file": template["license_file"],
            "json_valid": True,
            "node_count": len(nodes),
            "has_expected_node": template["expect_node"] in types,
            "weight_filenames": names,
            "expects": {
                "unet": template["expect_unet"],
                "lora": template["expect_lora"],
                "unet_present": template["expect_unet"] in names,
                "lora_present": template["expect_lora"] in names,
            },
            "closed_components_in_weight_filenames": closed,
            "labels": {
                "is_h3_max": False,
                "is_original_bf16_base": False,
                "is_context_ir": False,
                "is_regenerate_2k": False,
                "equivalent_quality_to_max": False,
            },
            "runner": "external_comfy_required",
            "installed": False,
            "inference_executed": False,
            "commercial_runtime": "disabled",
            "commercial_block": template["commercial_block"],
            "commercial_reason": _commercial_reason(template["commercial_block"]),
        })
    return reports


def _commercial_reason(kind):
    if kind == "h3":
        return (
            "JSON MIT covers the Comfy template only. The graph loads pruned INT8 Base weights, "
            "an NVFP4 Qwen3-VL encoder and a Turbo LoRA. The MiniMax H3 Community License is not MIT: "
            "EU/UK/US/South Korea exclusions cover works and outputs, UI attribution is required, "
            "and yearly product/service revenue above USD 20M needs separate consent. "
            "Commercial runtime stays off."
        )
    return (
        "The FireRed 1.0 workflow card is Apache-2.0. That does not clear the Qwen2.5-VL encoder, "
        "VAE, or Lightning LoRA 1.1 component terms. Commercial runtime stays off."
    )
