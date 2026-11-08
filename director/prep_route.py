"""Call the existing prep_photo command. This module does not reimplement rembg or Real-ESRGAN."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from director.errors import DirectorRefusal

# Measured on the supplied 1440×3120 phone-gallery screenshots.
# Row 280 is the black header, row 281 is the photo, row 2838 is the photo,
# row 2839 is the black footer. Cropping anywhere else is refused by returning None.
GALLERY_SIZE = (1440, 3120)
GALLERY_BOX = (0, 281, 1440, 2839)

FALLBACK_MARKERS = (
    "fallback на soft",
    "ONNX модель не найдена",
    "onnxruntime/numpy недоступны",
    "Real-ESRGAN не сработал",
    "Real-ESRGAN не запустился",
    "Real-ESRGAN бинарь не найден",
)

LIMITS = {
    "cutout": (
        "rembg u2net predicts an alpha mask. Kept RGB comes from the input. "
        "Edges are not a geometric guarantee. Alpha matting and decontamination flags are not passed."
    ),
    "upscale": (
        "Real-ESRGAN x4plus predicts detail. It runs only after a tight alpha crop, then the existing tool sets the long side to 3840. "
        "Printed letters can change. The native full-canvas cutout is kept unchanged for comparison. Label identity is not claimed. "
        "This upscale is not a beauty retouch: it does not soften wrinkles, whiten teeth, change expression, or restore a face."
    ),
    "beauty_retouch": False,
    "realesrgan_is_beauty_retouch": False,
    "generative_default": False,
    "denoise": "none",
    "alpha_matting": False,
    "decontamination": False,
    "text_redraw": False,
    "label_check": "not_measured",
    "alpha_bbox_threshold": 16,
    "alpha_bbox_pad": 1,
    "object_long_side_target": 3840,
}

SUBJECTS = ("person", "object", "generic")
SUBJECT_LIMITS = {
    **LIMITS,
    "subjects": list(SUBJECTS),
    "mask_model": "u2net",
    "u2net_human_seg": False,
    "face_restoration": False,
    "anatomy_invented": False,
    "generative_repaint": False,
    "identity_inferred": False,
    "bundled_portrait": None,
    "jpeg_white": "RGB composite of the alpha PNG on white, quality 92. No second upscale and no second rembg.",
}

ALPHA_BBOX_THRESHOLD = 16
ALPHA_BBOX_PAD = 1


def resolve_command(configured: str) -> str | None:
    if not configured:
        return None
    candidate = Path(configured)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(configured)


def gallery_chrome_crop(image: Image.Image) -> Image.Image | None:
    if image.size != GALLERY_SIZE:
        return None
    rgb = image.convert("RGB")
    if not _row_mostly_black(rgb, 280) or not _row_mostly_black(rgb, 2839):
        return None
    if _row_mostly_black(rgb, 281):
        return None
    return rgb.crop(GALLERY_BOX)


def build_argv(command: str, source: Path, output: Path, *, upscale: str, size: int, model: str = "u2net", already_cut: bool = False) -> list[str]:
    argv = [
        command,
        str(source),
        "-o",
        str(output),
        "--model",
        model,
        "--bg",
        "transparent",
        "--format",
        "png",
        "--size",
        str(size),
        "--upscale",
        upscale,
        "--denoise",
        "none",
    ]
    if already_cut:
        argv.append("--already-cut")
    return argv


def run_prep_command(argv: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise DirectorRefusal(
            "prep_timeout",
            "prep_photo exceeded %ss and was not started again." % timeout,
        ) from exc
    except OSError as exc:
        raise DirectorRefusal("prep_command_failed", "prep_photo could not be started: %s" % exc) from exc
    stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
    stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
    return completed.returncode, stdout, stderr


def onnx_fallback(stderr: str) -> bool:
    return any(marker in stderr for marker in FALLBACK_MARKERS)


def alpha_object_box(image: Image.Image, threshold: int = ALPHA_BBOX_THRESHOLD, pad: int = 0) -> tuple[int, int, int, int]:
    """Box of pixels with alpha above the halo threshold. right/bottom are exclusive."""
    mask = image.convert("RGBA").getchannel("A").point(lambda value: 255 if value > threshold else 0)
    box = mask.getbbox()
    if box is None:
        raise DirectorRefusal("subject_missing", "No product pixels above the alpha threshold.")
    left, top, right, bottom = box
    width, height = image.size
    if pad:
        left = max(0, left - pad)
        top = max(0, top - pad)
        right = min(width, right + pad)
        bottom = min(height, bottom + pad)
    if right <= left or bottom <= top:
        raise DirectorRefusal("subject_missing", "Alpha box collapsed.")
    return left, top, right, bottom


def measure_alpha(image: Image.Image, threshold: int = ALPHA_BBOX_THRESHOLD) -> dict:
    width, height = image.size
    left, top, right, bottom = alpha_object_box(image, threshold, pad=0)
    object_width = right - left
    object_height = bottom - top
    return {
        "threshold": threshold,
        "bbox": [left, top, right, bottom],
        "object_width": object_width,
        "object_height": object_height,
        "object_long_side": max(object_width, object_height),
        "canvas": [width, height],
        "margin_left": left,
        "margin_top": top,
        "margin_right": width - right,
        "margin_bottom": height - bottom,
    }


def assert_object_long_side(path: Path, target: int, threshold: int = ALPHA_BBOX_THRESHOLD) -> dict:
    with Image.open(path) as image:
        image.load()
        metrics = measure_alpha(image, threshold)
    minimum = int(target * 0.98)
    if metrics["object_long_side"] < minimum:
        raise DirectorRefusal(
            "object_below_target",
            "Object long side is %s inside a %sx%s canvas. Target is %s. Transparent margins were not accepted as 4K."
            % (metrics["object_long_side"], metrics["canvas"][0], metrics["canvas"][1], target),
        )
    return metrics


def validate_png(path: Path, *, expect_long_side: int | None, source_size: tuple[int, int]) -> dict:
    if not path.is_file():
        raise DirectorRefusal("prep_output_missing", "prep_photo did not write %s" % path.name)
    blob = path.read_bytes()
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise DirectorRefusal("prep_not_png", "%s is not a PNG" % path.name)
    with Image.open(path) as image:
        image.load()
        if image.mode != "RGBA":
            raise DirectorRefusal("alpha_missing", "%s has no alpha channel" % path.name)
        alpha = image.getchannel("A")
        low, high = alpha.getextrema()
        if high == 0:
            raise DirectorRefusal("subject_missing", "%s alpha is empty" % path.name)
        if low == 255:
            raise DirectorRefusal("alpha_missing", "%s has no transparent pixels" % path.name)
        width, height = image.size
    if expect_long_side is not None and max(width, height) != expect_long_side:
        raise DirectorRefusal(
            "long_side_mismatch",
            "%s long side is %s, expected %s. The image was not marked successful." % (path.name, max(width, height), expect_long_side),
        )
    if not _aspect_matches(source_size, (width, height)):
        raise DirectorRefusal(
            "aspect_stretched",
            "%s aspect does not match the source. A square stretch is not accepted." % path.name,
        )
    return {
        "file": path.name,
        "width": width,
        "height": height,
        "mode": "RGBA",
        "alpha_min": low,
        "alpha_max": high,
        "bytes": len(blob),
    }


def write_jpeg_white(source: Path, destination: Path, quality: int = 92) -> dict:
    """Composite an RGBA PNG onto white and save JPEG. This does not upscale and does not call rembg."""

    with Image.open(source) as image:
        image.load()
        if image.mode != "RGBA":
            raise DirectorRefusal("alpha_missing", "%s has no alpha channel to composite." % source.name)
        rgba = image.copy()
    white = Image.new("RGB", rgba.size, (255, 255, 255))
    white.paste(rgba, mask=rgba.getchannel("A"))
    _check_jpeg_pixels(rgba, white)
    destination.parent.mkdir(parents=True, exist_ok=True)
    white.save(destination, format="JPEG", quality=quality, optimize=True)
    blob = destination.read_bytes()
    if not blob.startswith(b"\xff\xd8\xff"):
        raise DirectorRefusal("jpeg_missing", "The white JPEG derivative is not a JPEG.")
    with Image.open(destination) as saved:
        saved.load()
        if saved.mode != "RGB" or saved.size != rgba.size:
            raise DirectorRefusal("jpeg_missing", "The white JPEG derivative does not match the PNG master.")
    return {
        "width": rgba.size[0],
        "height": rgba.size[1],
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "quality": quality,
        "background": "white",
        "alpha": False,
        "second_upscale": False,
    }


def _check_jpeg_pixels(rgba: Image.Image, white: Image.Image) -> None:
    source = rgba.load()
    flat = white.load()
    width, height = rgba.size
    seen_clear = False
    seen_solid = False
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = source[x, y]
            if alpha == 0:
                seen_clear = True
                if flat[x, y] != (255, 255, 255):
                    raise DirectorRefusal("jpeg_white_mismatch", "A transparent pixel was not white in the JPEG.")
            elif alpha == 255:
                seen_solid = True
                if flat[x, y] != (red, green, blue):
                    raise DirectorRefusal("jpeg_white_mismatch", "An opaque pixel changed in the JPEG.")
            if seen_clear and seen_solid:
                return
        if seen_clear and seen_solid:
            return


def _aspect_matches(source: tuple[int, int], output: tuple[int, int]) -> bool:
    sw, sh = source
    ow, oh = output
    if sw < 1 or sh < 1 or ow < 1 or oh < 1:
        return False
    return abs((sw / sh) - (ow / oh)) <= 0.02 * (sw / sh)


def _row_mostly_black(image: Image.Image, y: int) -> bool:
    width = image.size[0]
    dark = 0
    samples = 0
    for x in range(0, width, 8):
        red, green, blue = image.getpixel((x, y))
        samples += 1
        if (red + green + blue) / 3 < 25:
            dark += 1
    return samples > 0 and dark / samples >= 0.8
