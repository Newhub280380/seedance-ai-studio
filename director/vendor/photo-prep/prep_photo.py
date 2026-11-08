#!/usr/bin/env python3
"""Одна команда: вырез фона → апскейл → нормализация размера/формата."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR.parent
REALESRGAN_DIR = TOOLS_DIR / "realesrgan"
REALESRGAN_BIN = REALESRGAN_DIR / "realesrgan-ncnn-vulkan"
REALESRGAN_MODELS = REALESRGAN_DIR / "models"
ONNX_MODEL = Path(os.environ.get("PREP_ONNX_MODEL", REALESRGAN_DIR / "realesrgan_x4plus_fp16.onnx"))

REMBG_CANDIDATES = [
    Path.home() / ".local" / "bin" / "rembg",
    Path("/usr/local/bin/rembg"),
    Path("/usr/bin/rembg"),
]


def find_rembg() -> str:
    for p in REMBG_CANDIDATES:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    which = shutil.which("rembg")
    if which:
        return which
    raise SystemExit(
        "rembg не найден. Установите: pip install --user 'rembg[cli]' "
        "(и filetype, если CLI падает)."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="prep_photo",
        description="Подготовка одного фото: фон → чёткость → размер/формат",
    )
    p.add_argument("input", type=Path, help="Входной файл (jpg/png/webp)")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Куда сохранить (по умолчанию рядом: <имя>_prep.<fmt>)",
    )
    p.add_argument(
        "--size",
        type=int,
        default=2048,
        help="Длинная сторона в пикселях (0 = не трогать после апскейла)",
    )
    p.add_argument(
        "--format",
        choices=("png", "jpg", "jpeg"),
        default="png",
        help="Формат выхода",
    )
    p.add_argument(
        "--bg",
        choices=("white", "transparent"),
        default="white",
        help="Фон после выреза: белый или прозрачный",
    )
    p.add_argument(
        "--model",
        default="u2net",
        help="Модель rembg (u2net быстрее; birefnet-general точнее, тяжелее)",
    )
    p.add_argument(
        "--upscale",
        choices=("onnx", "soft", "realesrgan", "none"),
        default="onnx",
        help="onnx=Real-ESRGAN x4plus через onnxruntime CPU (дефолт); soft=LANCZOS×2+unsharp; realesrgan=ncnn-vulkan (нужен GPU); none",
    )
    p.add_argument(
        "--no-upscale",
        action="store_true",
        help="Синоним --upscale none",
    )
    p.add_argument(
        "--realesrgan-scale",
        type=int,
        choices=(2, 3, 4),
        default=2,
        help="Масштаб Real-ESRGAN (если выбран)",
    )
    p.add_argument(
        "--realesrgan-model",
        default="realesrgan-x4plus",
        help="Имя модели Real-ESRGAN",
    )
    p.add_argument(
        "--denoise",
        choices=("whatsapp", "none"),
        default="whatsapp",
        help="Предочистка сжатия/сетки WhatsApp (Gauss 0.8 + NL-means) перед вырезом и апскейлом",
    )
    p.add_argument("--tile", type=int, default=192, help="Размер тайла ONNX (меньше = меньше RAM)")
    p.add_argument("--tile-pad", type=int, default=16, help="Перекрытие тайлов ONNX")
    p.add_argument(
        "--already-cut",
        action="store_true",
        help="Вход уже вырезан (RGBA). Не вызывать rembg второй раз и не портить альфу denoise.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def log(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, file=sys.stderr)


def default_output(inp: Path, fmt: str) -> Path:
    ext = "jpg" if fmt in ("jpg", "jpeg") else "png"
    sibling_out = inp.parent / "out"
    if sibling_out.is_dir():
        return sibling_out / f"{inp.stem}_prep.{ext}"
    return inp.parent / f"{inp.stem}_prep.{ext}"


def rembg_cut(rembg: str, src: Path, dst: Path, model: str, verbose: bool) -> None:
    cmd = [rembg, "i", "-m", model, str(src), str(dst)]
    log(verbose, f"[1/3] rembg: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, capture_output=not verbose)
    except subprocess.CalledProcessError as e:
        # birefnet и тяжёлые модели могут не скачаться — пробуем u2net
        if model != "u2net":
            print(
                f"Модель {model} не сработала, пробую u2net…",
                file=sys.stderr,
            )
            cmd = [rembg, "i", "-m", "u2net", str(src), str(dst)]
            log(verbose, f"[1/3] rembg fallback: {' '.join(cmd)}")
            subprocess.run(cmd, check=True, capture_output=not verbose)
        else:
            raise SystemExit(f"rembg упал: {e}") from e


def soft_upscale(src: Path, dst: Path, verbose: bool) -> None:
    log(verbose, "[2/3] soft upscale: LANCZOS ×2 + unsharp")
    im = Image.open(src)
    w, h = im.size
    out = im.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
    # лёгкая резкость без «мыла»
    out = out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
    out.save(dst)


def onnx_upscale(src: Path, dst: Path, tile: int, pad: int, verbose: bool) -> bool:
    if not ONNX_MODEL.is_file():
        print(f"ВНИМАНИЕ: ONNX модель не найдена ({ONNX_MODEL}), fallback на soft.", file=sys.stderr)
        return False
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as e:
        print(f"ВНИМАНИЕ: onnxruntime/numpy недоступны ({e}), fallback на soft.", file=sys.stderr)
        return False
    log(verbose, f"[2/3] onnx Real-ESRGAN ×4: {ONNX_MODEL.name} tile={tile} pad={pad}")
    so = ort.SessionOptions()
    so.intra_op_num_threads = os.cpu_count() or 4
    sess = ort.InferenceSession(str(ONNX_MODEL), so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    itype = sess.get_inputs()[0].type
    dt = np.float16 if "float16" in itype else np.float32
    im = Image.open(src)
    alpha = None
    if im.mode == "RGBA":
        alpha = im.split()[-1]
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    h, w, _ = rgb.shape
    S = 4
    out = np.zeros((h * S, w * S, 3), dtype=np.float32)
    ny, nx = (h + tile - 1) // tile, (w + tile - 1) // tile
    n = 0
    for ty in range(ny):
        for tx in range(nx):
            y0, x0 = ty * tile, tx * tile
            y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
            py0, px0 = max(y0 - pad, 0), max(x0 - pad, 0)
            py1, px1 = min(y1 + pad, h), min(x1 + pad, w)
            patch = rgb[py0:py1, px0:px1].transpose(2, 0, 1)[None].astype(dt)
            res = sess.run(None, {iname: patch})[0][0].astype(np.float32).transpose(1, 2, 0)
            oy, ox = (y0 - py0) * S, (x0 - px0) * S
            out[y0 * S:y1 * S, x0 * S:x1 * S] = res[oy:oy + (y1 - y0) * S, ox:ox + (x1 - x0) * S]
            n += 1
            log(verbose, f"  tile {n}/{ny * nx}")
    res_im = Image.fromarray((np.clip(out, 0, 1) * 255.0 + 0.5).astype(np.uint8), "RGB")
    if alpha is not None:
        res_im.putalpha(alpha.resize(res_im.size, Image.Resampling.LANCZOS))
    res_im.save(dst)
    return True


def realesrgan_upscale(
    src: Path,
    dst: Path,
    scale: int,
    model: str,
    verbose: bool,
) -> bool:
    if not REALESRGAN_BIN.is_file():
        print("Real-ESRGAN бинарь не найден, fallback на soft.", file=sys.stderr)
        return False
    if not REALESRGAN_MODELS.is_dir():
        print("Папка models Real-ESRGAN не найдена, fallback на soft.", file=sys.stderr)
        return False
    cmd = [
        str(REALESRGAN_BIN),
        "-i",
        str(src),
        "-o",
        str(dst),
        "-n",
        model,
        "-s",
        str(scale),
        "-m",
        str(REALESRGAN_MODELS),
        "-f",
        "png",
    ]
    log(verbose, f"[2/3] realesrgan: {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"Real-ESRGAN не запустился ({e}), fallback на soft.", file=sys.stderr)
        return False
    if r.returncode != 0 or not dst.is_file():
        err = (r.stderr or r.stdout or "").strip()
        print(
            f"Real-ESRGAN не сработал ({err or 'нет выхода'}), fallback на soft.",
            file=sys.stderr,
        )
        return False
    return True


def normalize(
    src: Path,
    dst: Path,
    size: int,
    fmt: str,
    bg: str,
    verbose: bool,
) -> None:
    log(verbose, f"[3/3] normalize → {dst} size={size} format={fmt} bg={bg}")
    im = Image.open(src)
    if im.mode not in ("RGBA", "RGB"):
        im = im.convert("RGBA")

    if bg == "white":
        base = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode == "RGBA":
            base.paste(im, mask=im.split()[-1])
        else:
            base.paste(im)
        im = base
    else:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        if fmt in ("jpg", "jpeg"):
            print(
                "Прозрачный фон с JPEG несовместим — сохраняю PNG.",
                file=sys.stderr,
            )
            fmt = "png"
            dst = dst.with_suffix(".png")

    if size and size > 0:
        w, h = im.size
        long_side = max(w, h)
        if long_side != size:
            scale = size / float(long_side)
            nw = max(1, int(round(w * scale)))
            nh = max(1, int(round(h * scale)))
            im = im.resize((nw, nh), Image.Resampling.LANCZOS)

    dst.parent.mkdir(parents=True, exist_ok=True)
    if fmt in ("jpg", "jpeg"):
        if im.mode == "RGBA":
            bg_im = Image.new("RGB", im.size, (255, 255, 255))
            bg_im.paste(im, mask=im.split()[-1])
            im = bg_im
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.save(dst, format="JPEG", quality=92, optimize=True)
    else:
        im.save(dst, format="PNG", optimize=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inp = args.input.expanduser().resolve()
    if not inp.is_file():
        print(f"Нет файла: {inp}", file=sys.stderr)
        return 1

    fmt = "jpg" if args.format in ("jpg", "jpeg") else "png"
    out = (
        args.output.expanduser().resolve()
        if args.output
        else default_output(inp, fmt)
    )

    upscale_mode = "none" if args.no_upscale else args.upscale

    with tempfile.TemporaryDirectory(prefix="prep_photo_") as td:
        td_path = Path(td)
        cut = td_path / "cut.png"
        up = td_path / "up.png"

        if args.already_cut:
            log(args.verbose, "[1/3] rembg пропущен: вход уже вырезан")
            shutil.copy(inp, cut)
        else:
            rembg = find_rembg()
            src = inp
            if args.denoise == "whatsapp":
                import cv2
                log(args.verbose, "[0/3] denoise whatsapp: Gauss 0.8 + fastNlMeans")
                arr = cv2.imread(str(inp), cv2.IMREAD_COLOR)
                if arr is not None:
                    arr = cv2.fastNlMeansDenoisingColored(cv2.GaussianBlur(arr, (0, 0), 0.8), None, 5, 5, 7, 21)
                    src = td_path / "clean.png"
                    cv2.imwrite(str(src), arr)
            rembg_cut(rembg, src, cut, args.model, args.verbose)

        if upscale_mode == "none":
            log(args.verbose, "[2/3] upscale пропущен")
            mid = cut
        elif upscale_mode == "realesrgan":
            ok = realesrgan_upscale(
                cut,
                up,
                args.realesrgan_scale,
                args.realesrgan_model,
                args.verbose,
            )
            if ok:
                mid = up
            else:
                soft_upscale(cut, up, args.verbose)
                mid = up
        elif upscale_mode == "onnx":
            if not onnx_upscale(cut, up, args.tile, args.tile_pad, args.verbose):
                soft_upscale(cut, up, args.verbose)
            mid = up
        else:
            soft_upscale(cut, up, args.verbose)
            mid = up

        normalize(mid, out, args.size, fmt, args.bg, args.verbose)

    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
