"""Director adapter for the official Lightricks LTX-2.5 pipelines.

The model code stays an external pin. This module only builds the documented
CLI, checks whether this process can run it, and stores an MP4 when one exists.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from director.errors import DirectorRefusal

PIN = "4f521619ae0029e8fbb64d555030c2b121a33b4f"
REPO = "https://github.com/Lightricks/LTX-2"
LICENSE_WEIGHTS = "https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x"
LICENSE_CODE = "https://github.com/Lightricks/LTX-2/blob/main/LICENSE"
MODEL_CARD = "https://huggingface.co/Lightricks/LTX-2.5"
RELEASE_NOTE = "Public main on 2026-11-04, release tag text 1.6.1. README recommends LTX-2.5, not 2.3."

ATTACHMENT_A20 = (
    "To use LTX-2.x or Derivatives of LTX-2.x in any product, service, or application "
    "that directly competes with Licensor's commercial products or services, or is designed "
    "to replace or substitute Licensor's offerings in the market, without obtaining a separate "
    "commercial license from Licensor."
)

DISTILLED_FILES = (
    "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors",
    "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
    "vae/ltx-2.5-video-vae-bf16.safetensors",
    "vae/ltx-2.5-audio-vae-bf16.safetensors",
    "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
)
DFR_LORA = "loras/ltx-2.5-22b-ic-lora-pixel-spatial-upscaler-x2-1.0.safetensors"

# Official families. Image-to-video is not video-to-video.
FAMILIES = (
    {
        "id": "ltx.distilled",
        "module": "ltx_pipelines.distilled",
        "title": "LTX-2.5 Distilled: текст или первый кадр",
        "temporal_edit": False,
        "summary": "Text-to-video, or image-to-video when --image is a still at a frame index. Not a video object replacement.",
    },
    {
        "id": "ltx.dfr",
        "module": "ltx_pipelines.dfr_pipeline",
        "title": "LTX-2.5 DFR: качество, ключевые кадры детализации",
        "temporal_edit": False,
        "summary": "Production text/image-to-video using the distilled transformer plus the detailing IC-LoRA. Not a masked video edit.",
    },
    {
        "id": "ltx.ic_lora",
        "module": "ltx_pipelines.ic_lora",
        "title": "LTX-2.5 IC-LoRA: видео по референс-видео",
        "temporal_edit": True,
        "summary": "Official video-to-video: --video-conditioning is required. Optional grayscale mask video and still keyframes. The IC-LoRA decides what the reference controls. This is not Parrot multi-image product swap and does not lock packaging text.",
    },
    {
        "id": "ltx.retake",
        "module": "ltx_pipelines.retake",
        "title": "LTX-2.5 Retake: переснять интервал времени",
        "temporal_edit": True,
        "summary": "Regenerate one time window of a source video from a prompt. Outside that window stays. This is not a spatial object mask.",
    },
)

MEASURED_HOSTS = {
    "measured_at": "2026-11-07",
    "not_a_live_reprobe": True,
    "box": "No nvidia-smi, no /dev/nvidia0, 15 GiB RAM, 89 GiB disk free, no torch in system Python.",
    "pc_ii": "RTX 2060 6144 MiB (4373 MiB free), 16 GiB RAM (6.17 GiB free), C free 1.60 GiB, G free 1.52 GiB. Volumes are not additive.",
    "disk_block": "About 66 GiB of LTX-2.5 base files cannot be placed on the measured PC free space.",
    "vram_note": (
        "The official README documents --quantization fp8-cast and --offload cpu or disk for tight GPU memory. "
        "It does not publish a minimum VRAM figure. 6 GiB is therefore not declared impossible from file size alone, and it is not declared sufficient."
    ),
}


def family(capability_id: str) -> dict | None:
    for item in FAMILIES:
        if item["id"] == capability_id:
            return item
    return None


def required_files(capability_id: str) -> tuple[str, ...]:
    files = list(DISTILLED_FILES)
    if capability_id == "ltx.dfr":
        files.append(DFR_LORA)
    return tuple(files)


def probe(model_root: str | None, python_bin: str) -> dict:
    missing: list[str] = []
    gpu = _gpu()
    if not gpu["available"]:
        missing.append("cuda_gpu: " + gpu["reason"])
    module = _module_status(python_bin)
    if not module["importable"]:
        missing.append("python_module ltx_pipelines: " + module["reason"])
    root = Path(model_root).expanduser() if model_root else None
    absent = []
    if root is None or not root.is_dir():
        missing.append("LTX_MODEL_ROOT directory")
    else:
        for relative in DISTILLED_FILES:
            if not (root / relative).is_file():
                absent.append(relative)
        if absent:
            missing.append("weight_files: " + ", ".join(absent))
    return {
        "ready": not missing,
        "missing": missing,
        "gpu": gpu,
        "module": module,
        "model_root": str(root) if root else None,
        "pin": PIN,
        "measured_hosts": MEASURED_HOSTS,
        "offload": "Official flags when a CUDA process exists: --quantization fp8-cast --offload cpu|disk. They are not passed unless the task asks.",
        "weights_downloaded_by_director": False,
    }


def build_argv(python_bin: str, spec: dict, paths: dict, output: Path, body: dict) -> list[str]:
    root = Path(body["model_root"])
    argv = [
        python_bin,
        "-m",
        spec["module"],
        "--transformer-path",
        str(root / DISTILLED_FILES[0]),
        "--text-encoder-path",
        str(root / DISTILLED_FILES[1]),
        "--video-vae-path",
        str(root / DISTILLED_FILES[2]),
        "--audio-vae-path",
        str(root / DISTILLED_FILES[3]),
        "--spatial-upsampler-path",
        str(root / DISTILLED_FILES[4]),
        "--prompt",
        body["prompt"],
        "--output-path",
        str(output),
        "--seed",
        str(body["seed"]),
    ]
    mode = spec["id"]
    if mode != "ltx.retake":
        argv.extend(["--num-frames", str(body["num_frames"])])
    if mode == "ltx.dfr":
        argv.extend(["--detailing-lora", str(root / DFR_LORA)])
    if mode == "ltx.ic_lora":
        argv.extend(["--video-conditioning", str(paths["video"]), str(body.get("video_strength", 1.0))])
        argv.extend(["--lora", str(paths["lora"]), str(body.get("lora_strength", 1.0))])
        if paths.get("mask"):
            argv.extend(["--conditioning-attention-mask", str(paths["mask"]), str(body.get("mask_strength", 1.0))])
    if mode == "ltx.retake":
        argv.extend([
            "--video-path",
            str(paths["video"]),
            "--start-time",
            str(body["start_time"]),
            "--end-time",
            str(body["end_time"]),
        ])
    for image in body.get("images") or []:
        argv.extend(["--image", str(image["path"]), str(image["frame"]), str(image["strength"])])
    if body.get("quantization") == "fp8-cast":
        argv.extend(["--quantization", "fp8-cast"])
    if body.get("offload") in ("cpu", "disk"):
        argv.extend(["--offload", body["offload"]])
    return argv


def run_once(argv: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout, check=False, env=os.environ.copy())
    except subprocess.TimeoutExpired as exc:
        raise DirectorRefusal("ltx_timeout", "LTX process exceeded %ss and was not started again." % timeout) from exc
    except OSError as exc:
        raise DirectorRefusal("ltx_failed", "LTX process could not start: %s" % exc) from exc
    return (
        completed.returncode,
        (completed.stdout or b"").decode("utf-8", errors="replace"),
        (completed.stderr or b"").decode("utf-8", errors="replace"),
    )


def is_mp4(path: Path) -> bool:
    if not path.is_file():
        return False
    blob = path.read_bytes()
    return len(blob) >= 12 and blob[4:8] == b"ftyp"


def _gpu() -> dict:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return {"available": False, "reason": "nvidia-smi is not on PATH"}
    try:
        completed = subprocess.run([binary, "-L"], capture_output=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": "nvidia-smi failed: %s" % exc}
    text = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
    if completed.returncode != 0 or not text or "NVIDIA" not in text.upper():
        return {"available": False, "reason": "nvidia-smi returned no GPU"}
    return {"available": True, "reason": text.splitlines()[0][:180]}


def _module_status(python_bin: str) -> dict:
    if not python_bin or shutil.which(python_bin) is None and not Path(python_bin).is_file():
        return {"importable": False, "reason": "LTX_PYTHON is not executable"}
    try:
        completed = subprocess.run(
            [python_bin, "-c", "import importlib.util; spec = importlib.util.find_spec('ltx_pipelines'); print('yes' if spec else 'no')"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"importable": False, "reason": str(exc)}
    out = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
    if completed.returncode == 0 and out.endswith("yes"):
        return {"importable": True, "reason": "ltx_pipelines importable"}
    err = (completed.stderr or b"").decode("utf-8", errors="replace")[-240:]
    return {"importable": False, "reason": err or "ltx_pipelines is not installed"}
