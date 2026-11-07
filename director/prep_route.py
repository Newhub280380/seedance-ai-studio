"""Call the existing prep_photo command. This module does not reimplement rembg or Real-ESRGAN."""

from __future__ import annotations

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
        "Real-ESRGAN x4plus predicts detail, then the existing tool fits the long side to the requested size with LANCZOS. "
        "Printed letters can change. The native cutout is kept so the two files can be compared. Label identity is not claimed."
    ),
    "denoise": "none",
    "alpha_matting": False,
    "decontamination": False,
    "text_redraw": False,
    "label_check": "not_measured",
}


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


def build_argv(command: str, source: Path, output: Path, *, upscale: str, size: int, model: str = "u2net") -> list[str]:
    return [
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
