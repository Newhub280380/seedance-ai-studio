"""Freefield-style apps for Режиссёр: one app per function, each wired to a real fal endpoint or marked «скоро».

Mirrors the organization of github.com/neuregex/freefield (apps/<id>/app.json: section, inputs, options),
but runs the open models on fal hosted endpoints because the PC has no local NVIDIA GPU.

Self-contained on purpose (stdlib + requests-free core): the catalog, input validation, payload building and
dry-run need nothing from Режиссёр 2.0, so Режиссёр 1.0 (seedance-ai-studio) and BEAUTYART HQ can reuse this
file as is. Only ``start_run`` needs a fal client object with ``call(model, body, cancelled) -> dict``
(Режиссёр 2.0 passes ``cloud_generation.Fal()``) and a ``file_to_url`` function (data URI).

Prices are copied from the fal model pages (checked 2026-11-14) and are shown to the person before a paid run.
Nothing in this module submits anything by itself: ``dry_run`` only builds the request.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Callable

CHECKED = "2026-11-14"
MAX_VIDEO_MB = 40  # sent inline as a data URI, same cap as the Wan 2.2 cloud engine
MAX_IMAGE_MB = 20
MAX_AUDIO_MB = 40

SECTIONS = [
    {"id": "video", "label": "Видео"},
    {"id": "image", "label": "Фото"},
    {"id": "audio", "label": "Звук"},
    {"id": "utility", "label": "Помощники"},
]

ASPECT = ["auto", "16:9", "9:16"]
RES_WAN = ["480p", "580p", "720p"]
LANGS = ["en", "ru", "kk", "es", "de", "fr", "it", "pt", "tr", "ar", "zh", "ja", "ko", "uk", "pl", "hi"]


def _i(id, type, label, *, required=False, default=None, options=None, min=None, max=None, step=None, help=""):
    spec = {"id": id, "type": type, "label": label, "required": required}
    for key, value in (("default", default), ("options", options), ("min", min), ("max", max),
                       ("step", step), ("help", help)):
        if value not in (None, ""):
            spec[key] = value
    return spec


def _seed(v):
    try:
        s = int(v)
    except (TypeError, ValueError):
        return None
    return s if s >= 0 else None


def _put_seed(body, v):
    s = _seed(v.get("seed"))
    if s is not None:
        body["seed"] = s
    return body


# ---------------------------------------------------------------- payload builders (v = validated values, u = file -> url)

def _wan_animate(v, u):
    return _put_seed({"video_url": u(v["clip"]), "image_url": u(v["character"]), "resolution": v["resolution"],
                      "use_turbo": bool(v["turbo"]), "video_quality": "high"}, v)


def _speak(v, u):
    scene = (v.get("scene") or "").strip()
    prompt = f'{scene + ". " if scene else ""}The person looks into the camera and says, in {v["voice"]}: "{v["line"].strip()}"'
    return {"image_url": u(v["image"]), "prompt": prompt, "generate_audio": True, "duration": int(v["duration"]),
            "resolution": v["resolution"], "aspect_ratio": v["aspect"], "fps": 25}


def _talking(v, u):
    body = {"image_url": u(v["portrait"]), "audio_url": u(v["audio"]), "aspect_ratio": v["aspect"]}
    if (v.get("prompt") or "").strip():
        body["prompt"] = v["prompt"].strip()
    return body


def _seedvr_video(v, u):
    body = {"video_url": u(v["clip"]), "output_format": "X264 (.mp4)", "output_quality": "high"}
    if v["target"] == "x2":
        body.update(upscale_mode="factor", upscale_factor=2)
    else:
        body.update(upscale_mode="target", target_resolution=v["target"])
    return _put_seed(body, v)


def _seedvr_image(v, u):
    return _put_seed({"image_url": u(v["image"]), "upscale_mode": "factor", "upscale_factor": int(v["scale"]),
                      "output_format": "png"}, v)


def _veed_dub(v, u):
    return {"video_url": u(v["clip"]), "target_language": v["target_language"], "source_language": v["source_language"]}


def _ltx_i2v(v, u):
    return {"image_url": u(v["image"]), "prompt": v["prompt"].strip(), "generate_audio": bool(v["sound"]),
            "duration": int(v["duration"]), "resolution": v["resolution"], "aspect_ratio": v["aspect"], "fps": 25}


def _ltx_t2v(v, u):
    return {"prompt": v["prompt"].strip(), "generate_audio": bool(v["sound"]), "duration": int(v["duration"]),
            "resolution": v["resolution"], "aspect_ratio": v["aspect"], "fps": 25}


def _klein(v, u):
    return _put_seed({"prompt": v["prompt"].strip(), "image_size": v["size"], "num_inference_steps": int(v["steps"]),
                      "output_format": "png", "num_images": 1}, v)


def _klein_edit(v, u):
    return _put_seed({"prompt": v["scene"].strip(), "image_urls": [u(v["reference"])],
                      "num_inference_steps": int(v["steps"]), "output_format": "png", "num_images": 1}, v)


ANGLE_MOVES = {  # label -> (horizontal_angle, vertical_angle, zoom) for fal's Multiple Angles LoRA
    "Повернуть камеру на 45° влево": (45, 0, 5),
    "Повернуть камеру на 45° вправо": (315, 0, 5),
    "Обойти сбоку (90°)": (90, 0, 5),
    "Сзади (180°)": (180, 0, 5),
    "Сверху (вид вниз)": (0, 60, 5),
    "Снизу (вид вверх)": (0, -30, 5),
    "Ближе": (0, 0, 8),
    "Дальше": (0, 0, 2),
}


def _angles(v, u):
    h, vert, zoom = ANGLE_MOVES[v["move"]]
    body = {"image_urls": [u(v["image"])], "horizontal_angle": h, "vertical_angle": vert, "zoom": zoom,
            "output_format": "png", "num_images": 1}
    if (v.get("details") or "").strip():
        body["additional_prompt"] = v["details"].strip()
    return _put_seed(body, v)


NEXT_SCENES = {
    "Камера отъезжает, видно больше вокруг": "the camera pulls back to reveal the wider surroundings",
    "Камера наезжает, крупный план героя": "the camera pushes in to a close-up on the main subject",
    "Панорама вправо": "the camera pans right, revealing more of the scene",
    "В кадр входит новый герой слева": "a new character walks into the frame from the left",
    "Прошло время, сумерки, зажигается свет": "time passes and the light turns to dusk, lights coming on",
    "Погода меняется, начинается дождь": "the weather turns and rain begins to fall",
    "Камера поднимается к небу": "the camera tilts up toward the sky",
    "Обратная точка (reverse angle)": "the camera cuts to the reverse angle, looking back the other way",
}


def _next_scene(v, u):
    text = "Next Scene: " + NEXT_SCENES[v["next"]]
    if (v.get("details") or "").strip():
        text += ". " + v["details"].strip()
    return _put_seed({"image_urls": [u(v["image"])], "prompt": text, "output_format": "png", "num_images": 1}, v)


def _krea_style(v, u):
    return _put_seed({"prompt": v["prompt"].strip(), "reference_image_urls": [u(v["style"])],
                      "style_scale": float(v["strength"]), "image_size": v["size"], "output_format": "png",
                      "enable_prompt_expansion": bool(v["enhance"]), "num_images": 1}, v)


def _zimage(v, u):
    return _put_seed({"prompt": v["prompt"].strip(), "image_size": v["size"], "num_inference_steps": int(v["steps"]),
                      "output_format": "png", "num_images": 1}, v)


def _music(v, u):
    lyrics = (v.get("lyrics") or "").strip() or "[Instrumental]"
    return _put_seed({"prompt": v["style"].strip(), "lyrics": lyrics, "duration": float(v["seconds"])}, v)


def _sfx(v, u):
    return _put_seed({"prompt": v["description"].strip(), "duration": float(v["seconds"]), "output_format": "mp3"}, v)


def _extend(v, u):
    return _put_seed({"video_url": u(v["clip"]), "prompt": v["prompt"].strip(), "duration": float(v["duration"]),
                      "resolution": v["resolution"], "output": "extended"}, v)


def _birefnet(v, u):
    return {"image_url": u(v["image"]), "model": v["model"], "output_format": "png", "refine_foreground": True}


def _birefnet_video(v, u):
    return {"video_url": u(v["clip"]), "model": v["model"], "output_mask": bool(v["mask"]),
            "video_output_type": "X264 (.mp4)"}


def _sam3(v, u):
    return {"video_url": u(v["clip"]), "prompt": v["target"].strip(), "apply_mask": True}


# ---------------------------------------------------------------- catalog

def _app(id, name, section, summary, *, freefield, models, status, inputs=(), endpoint=None, build=None,
         price=None, output="video", needs=None, priority=False, alt_of=None):
    return {"id": id, "name": name, "section": section, "summary": summary, "freefield_app": freefield,
            "models": models, "status": status, "inputs": list(inputs), "endpoint": endpoint, "build": build,
            "price": price, "output": output, "needs": needs, "priority": priority, "alt_of": alt_of}


SEED = _i("seed", "seed", "Seed (-1 = случайно)", default=-1)

APPS = [
    # ---------- the five priority functions
    _app("recast", "Замена персонажа (Recast)", "video",
         "Ваш персонаж вместо человека в видео, движения сохраняются.",
         freefield="recast", models="Wan 2.2 Animate 14B (replace)", status="ready", priority=True,
         endpoint="fal-ai/wan/v2.2-14b/animate/replace", build=_wan_animate,
         price="$0.04/с при 480p, $0.06/с при 580p, $0.08/с при 720p (секунды готового видео)",
         inputs=[_i("clip", "video", "Видео с человеком", required=True, help="до 40 МБ"),
                 _i("character", "image", "Ваш персонаж", required=True, help="один человек, в полный рост если в видео полный рост"),
                 _i("resolution", "select", "Качество", default="480p", options=RES_WAN),
                 _i("turbo", "toggle", "Быстрый режим (turbo)", default=True), SEED]),
    _app("recast-scail2", "Recast на SCAIL-2 + SAM 3.1 (как во Freefield)", "video",
         "Точная замена выбранного человека по описанию («женщина в шляпе») с трекингом SAM 3.1.",
         freefield="recast", models="SCAIL-2 + SAM 3.1 + Wan 2.1", status="soon", alt_of="recast",
         needs="У fal SCAIL-2 есть в каталоге, но публичного API-описания пока нет (страница 404). Нужен GPU-сервер "
               "(RunPod) с ComfyUI и workflow Freefield apps/recast/workflow.api.json. Сейчас работает «Замена персонажа» на Wan Animate."),
    _app("motion", "Перенос движения (Wan Animate)", "video",
         "Оживляет картинку персонажа движениями из другого ролика.",
         freefield="motion-transfer", models="Wan 2.2 Animate 14B (move)", status="ready", priority=True,
         endpoint="fal-ai/wan/v2.2-14b/animate/move", build=_wan_animate,
         price="$0.04/с при 480p, $0.06/с при 580p, $0.08/с при 720p (секунды готового видео)",
         inputs=[_i("character", "image", "Персонаж", required=True),
                 _i("clip", "video", "Ролик с движением", required=True, help="до 40 МБ"),
                 _i("resolution", "select", "Качество", default="480p", options=RES_WAN),
                 _i("turbo", "toggle", "Быстрый режим (turbo)", default=True), SEED]),
    _app("dub", "Переозвучка на другой язык (DubIt)", "video",
         "Человек в ролике говорит новый текст на любом языке: новый голос и новые движения губ.",
         freefield="dub", models="LTX-2.5 + DubIt IC-LoRA", status="soon", priority=True,
         needs="Открытой DubIt-LoRA на fal нет. Нужен GPU-сервер (RunPod) с ComfyUI, LTX-2.5 и workflow Freefield "
               "apps/dub/workflow.api.json. Пока можно «Дубляж (облако VEED)» ниже."),
    _app("dub-cloud", "Дубляж (облако VEED)", "video",
         "Переводит речь в ролике на другой язык с подгонкой губ (готовый облачный сервис, не DubIt).",
         freefield="dub", models="VEED Dubbing", status="ready", alt_of="dub",
         endpoint="veed/dubbing", build=_veed_dub, price="$0.11 за секунду исходного видео",
         inputs=[_i("clip", "video", "Ролик с речью", required=True, help="до 40 МБ"),
                 _i("target_language", "select", "На какой язык", required=True, default="en", options=LANGS),
                 _i("source_language", "select", "Исходный язык", default="auto", options=["auto"] + LANGS)]),
    _app("speak", "Говорящее фото (текст)", "video",
         "Фото произносит вашу фразу: голос, губы и движение за один проход, без отдельной озвучки.",
         freefield="speak", models="LTX-2.5 (видео со звуком)", status="ready", priority=True,
         endpoint="lightricks/ltx-2.5/image-to-video/fast", build=_speak,
         price="$0.09/с при 720p, $0.13/с при 1080p (звук включён)",
         inputs=[_i("image", "image", "Портрет", required=True),
                 _i("line", "textarea", "Что говорит", required=True),
                 _i("voice", "select", "Голос", default="a warm, friendly female voice",
                    options=["a warm, friendly female voice", "a calm, deep male voice", "an excited, energetic voice",
                             "a soft, close whisper", "the clear, confident voice of a news anchor"]),
                 _i("scene", "textarea", "Кадр (необязательно)", default="Medium close-up, natural head movement and blinking, soft window light"),
                 _i("duration", "select", "Секунды", default=6, options=[6, 8, 10]),
                 _i("resolution", "select", "Качество", default="720p", options=["720p", "1080p"]),
                 _i("aspect", "select", "Формат", default="9:16", options=ASPECT)]),
    _app("talking", "Говорящее фото (свой звук)", "video",
         "Портрет говорит или поёт под ваш голос/песню.",
         freefield="talking", models="LTX-2.5 audio-to-video", status="ready", priority=True,
         endpoint="lightricks/ltx-2.5/audio-to-video/fast", build=_talking,
         price="$0.13 за секунду входного звука (1080p)",
         inputs=[_i("portrait", "image", "Портрет", required=True),
                 _i("audio", "audio", "Голос или песня", required=True, help="длина звука = длина видео"),
                 _i("prompt", "textarea", "Как исполняет (необязательно)",
                    default="She speaks warmly to the camera with natural head movement and expressive eyes, soft studio light"),
                 _i("aspect", "select", "Формат", default="9:16", options=ASPECT)]),
    _app("upscale-video", "Улучшить видео до 4K (SeedVR2)", "video",
         "Восстанавливает детали и увеличивает разрешение ролика.",
         freefield="upscale-video", models="SeedVR2", status="ready", priority=True,
         endpoint="fal-ai/seedvr/upscale/video", build=_seedvr_video,
         price="$0.001 за мегапиксель готового видео (ширина×высота×кадры); 5 с 4K при 24 к/с ≈ $1.00",
         inputs=[_i("clip", "video", "Ролик", required=True, help="до 40 МБ"),
                 _i("target", "select", "Итог", default="2160p", options=["x2", "1080p", "1440p", "2160p"],
                    help="2160p = 4K"), SEED]),
    _app("upscale", "Улучшить фото (SeedVR2)", "image",
         "Восстанавливает детали и увеличивает фото в 2 или 4 раза.",
         freefield="upscale", models="SeedVR2", status="ready",
         endpoint="fal-ai/seedvr/upscale/image", build=_seedvr_image, output="image",
         price="$0.001 за мегапиксель результата",
         inputs=[_i("image", "image", "Фото", required=True), _i("scale", "select", "Во сколько раз", default=2, options=[2, 4]), SEED]),

    # ---------- the rest of the Freefield set
    _app("video", "Видео со звуком из фото (LTX-2.5)", "video",
         "Оживляет кадр с синхронным звуком.", freefield="video", models="LTX-2.5", status="ready",
         endpoint="lightricks/ltx-2.5/image-to-video/fast", build=_ltx_i2v,
         price="$0.09/с при 720p, $0.13/с при 1080p (звук включён)",
         inputs=[_i("image", "image", "Кадр", required=True), _i("prompt", "textarea", "Что происходит", required=True),
                 _i("sound", "toggle", "Со звуком", default=True),
                 _i("duration", "select", "Секунды", default=6, options=[6, 8, 10]),
                 _i("resolution", "select", "Качество", default="720p", options=["720p", "1080p"]),
                 _i("aspect", "select", "Формат", default="auto", options=ASPECT)]),
    _app("video-t2v", "Видео со звуком из текста (LTX-2.5)", "video",
         "Сцена со звуком по описанию.", freefield="video-t2v", models="LTX-2.5", status="ready",
         endpoint="lightricks/ltx-2.5/text-to-video/fast", build=_ltx_t2v,
         price="$0.09/с при 720p, $0.13/с при 1080p (звук включён)",
         inputs=[_i("prompt", "textarea", "Описание сцены", required=True), _i("sound", "toggle", "Со звуком", default=True),
                 _i("duration", "select", "Секунды", default=6, options=[6, 8, 10]),
                 _i("resolution", "select", "Качество", default="720p", options=["720p", "1080p"]),
                 _i("aspect", "select", "Формат", default="9:16", options=["16:9", "9:16"])]),
    _app("prompt-prep", "Подготовка промпта для LTX (Gemma 4)", "utility",
         "Расписывает короткую идею в подробный промпт для LTX.", freefield="video (enhance)", models="Gemma 4",
         status="soon", needs="Подключить к уже работающему помощнику Режиссёра (ERNIE/GLM) как шаг «улучшить промпт» "
                              "или поднять Gemma 4 на GPU-сервере. Денег за генерацию не требует."),
    _app("extend-video", "Продлить видео (MiniMax H3)", "video",
         "Продолжает ролик с последнего кадра, со звуком.", freefield="extend-video", models="MiniMax H3 Max",
         status="ready", endpoint="minimax/h3-max/extend-video", build=_extend,
         price="$0.05/с при 480P, $0.08/с при 768P, $0.16/с при 1080P (секунды новой части)",
         inputs=[_i("clip", "video", "Ваш ролик", required=True, help="до 40 МБ"),
                 _i("prompt", "textarea", "Что дальше", required=True,
                    default="The scene continues naturally: same people, place, light and camera style."),
                 _i("duration", "slider", "Сколько секунд добавить", default=5, min=2, max=10, step=1),
                 _i("resolution", "select", "Качество", default="768P", options=["480P", "768P", "1080P"]), SEED]),
    _app("reshoot", "Переснять с другого ракурса (H3 + MoGe)", "video",
         "Двигает камеру на новую точку в готовом ролике.", freefield="reshoot", models="MiniMax H3 + MoGe + CrossView",
         status="soon", needs="Во Freefield это цепочка MoGe → CrossView → H3 на своём GPU. На fal есть отдельные "
                              "кусочки (MoGe, H3 camera-controls), сборку цепочки сделаю следующим шагом."),
    _app("wan-effects", "Эффекты камеры Wan (Bullet Time и др.)", "video",
         "Заморозка времени и облёт камеры вокруг героя, точные движения камеры.",
         freefield="bullet-time-scene, camera-moves", models="Wan 2.1 / Wan 2.2 Fun Camera + LoRA эффектов", status="soon",
         needs="Нужны файлы LoRA эффектов из Freefield и GPU-сервер (RunPod/ComfyUI). Облачный Wan-с-LoRA на fal "
               "помечен к удалению — на него не завязываюсь."),
    _app("video-remove-bg", "Убрать фон в видео (BiRefNet)", "video",
         "Вырезает человека/предмет в каждом кадре.", freefield="video-remove-bg", models="BiRefNet v2",
         status="ready", endpoint="fal-ai/birefnet/v2/video", build=_birefnet_video,
         price="по секундам работы GPU; на странице fal указано $0 за секунду",
         inputs=[_i("clip", "video", "Ролик", required=True, help="до 40 МБ"),
                 _i("model", "select", "Модель", default="General Use (Light)",
                    options=["General Use (Light)", "General Use (Heavy)", "Portrait", "Matting"]),
                 _i("mask", "toggle", "Отдельно маска", default=False)]),
    _app("track", "Трекинг объекта в видео (SAM 3)", "video",
         "Находит и ведёт по кадрам объект по описанию («woman», «red car»).", freefield="recast (SAM 3.1)",
         models="SAM 3 (fal; во Freefield SAM 3.1)", status="ready", endpoint="fal-ai/sam-3/video", build=_sam3,
         price="$0.005 за каждые 16 кадров",
         inputs=[_i("clip", "video", "Ролик", required=True, help="до 40 МБ"),
                 _i("target", "text", "Что отслеживать (по-английски)", required=True, default="person")]),
    _app("face-detect", "Поиск лиц (MediaPipe)", "utility",
         "Находит лица в кадре для кадрирования и замены.", freefield="(служебный шаг)", models="MediaPipe",
         status="soon", needs="Работает на обычном процессоре без GPU: нужно поставить пакет mediapipe в окружение "
                              "Режиссёра (с вашего «да») и добавить шаг. Платных запросов нет."),
    _app("remove-bg", "Убрать фон на фото (BiRefNet)", "image",
         "PNG с прозрачностью.", freefield="remove-bg", models="BiRefNet v2", status="ready",
         endpoint="fal-ai/birefnet/v2", build=_birefnet, output="image",
         price="по секундам работы GPU; на странице fal указано $0 за секунду",
         inputs=[_i("image", "image", "Фото", required=True),
                 _i("model", "select", "Модель", default="General Use (Light)",
                    options=["General Use (Light)", "General Use (Heavy)", "Portrait", "Matting"])]),
    _app("create-image", "Картинка из текста (FLUX Klein)", "image",
         "Быстрая картинка по описанию.", freefield="create-image", models="FLUX.2 Klein 9B", status="ready",
         endpoint="fal-ai/flux-2/klein/9b", build=_klein, output="image", price="$0.006 за мегапиксель",
         inputs=[_i("prompt", "textarea", "Описание", required=True),
                 _i("size", "select", "Формат", default="portrait_16_9",
                    options=["square_hd", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9"]),
                 _i("steps", "slider", "Шаги", default=4, min=4, max=8, step=1), SEED]),
    _app("character", "Персонаж в новой сцене (FLUX Klein)", "image",
         "Человек с вашего фото в новой сцене, внешность сохраняется.", freefield="character",
         models="FLUX.2 Klein 9B edit", status="ready", endpoint="fal-ai/flux-2/klein/9b/edit", build=_klein_edit,
         output="image", price="$0.011 за мегапиксель (вход + выход)",
         inputs=[_i("reference", "image", "Фото человека", required=True), _i("scene", "textarea", "Новая сцена", required=True),
                 _i("steps", "slider", "Шаги", default=4, min=4, max=8, step=1), SEED]),
    _app("angles", "Другой ракурс (Qwen Edit)", "image",
         "Та же сцена с другой точки камеры.", freefield="angles", models="Qwen-Image-Edit 2511 + Multiple Angles LoRA",
         status="ready", endpoint="fal-ai/qwen-image-edit-2511-multiple-angles", build=_angles, output="image",
         price="$0.035 за мегапиксель",
         inputs=[_i("image", "image", "Кадр", required=True),
                 _i("move", "select", "Куда камера", default="Повернуть камеру на 45° влево", options=list(ANGLE_MOVES)),
                 _i("details", "textarea", "Уточнение (необязательно)"), SEED]),
    _app("whats-next", "Следующая сцена (Qwen Edit)", "image",
         "Следующий кадр вашей сцены.", freefield="whats-next", models="Qwen-Image-Edit + Next Scene LoRA",
         status="ready", endpoint="fal-ai/qwen-image-edit-plus-lora-gallery/next-scene", build=_next_scene,
         output="image", price="$0.035 за мегапиксель",
         inputs=[_i("image", "image", "Кадр", required=True),
                 _i("next", "select", "Что дальше", default=list(NEXT_SCENES)[0], options=list(NEXT_SCENES)),
                 _i("details", "textarea", "Уточнение (необязательно)"), SEED]),
    _app("relight", "Переосвещение фото (Qwen Edit)", "image",
         "Новый свет: направление, мягкость, цвет.", freefield="relight", models="Qwen-Image-Edit + Relight LoRA",
         status="soon", needs="Отдельного relight-эндпоинта Qwen на fal нет; нужен ComfyUI-workflow Freefield apps/relight на GPU-сервере."),
    _app("style-reference", "Стиль по образцу (Krea 2)", "image",
         "Рисует что угодно в стиле вашей картинки-образца.", freefield="style-reference, styles",
         models="Krea 2 Turbo + style LoRA", status="ready", endpoint="fal-ai/krea-2/turbo/style", build=_krea_style,
         output="image", price="$0.01 за мегапиксель",
         inputs=[_i("style", "image", "Образец стиля", required=True), _i("prompt", "textarea", "Что нарисовать", required=True),
                 _i("strength", "slider", "Сила стиля", default=1.0, min=0, max=2, step=0.1),
                 _i("size", "select", "Формат", default="square_hd",
                    options=["square_hd", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9"]),
                 _i("enhance", "toggle", "Улучшить промпт", default=False), SEED]),
    _app("z-image", "Быстрая картинка (Z-Image Turbo)", "image",
         "Очень быстрая картинка за 8 шагов.", freefield="z-image", models="Z-Image Turbo", status="ready",
         endpoint="fal-ai/z-image/turbo", build=_zimage, output="image", price="$0.005 за мегапиксель",
         inputs=[_i("prompt", "textarea", "Описание", required=True),
                 _i("size", "select", "Формат", default="portrait_16_9",
                    options=["square_hd", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9"]),
                 _i("steps", "slider", "Шаги", default=8, min=1, max=8, step=1), SEED]),
    _app("music", "Песня (MiniMax Music 3)", "audio",
         "Полная песня по стилю и (необязательно) тексту.", freefield="music", models="MiniMax Music 3",
         status="ready", endpoint="minimax/music-3", build=_music, output="audio",
         price="$0.002 за секунду музыки (минута ≈ $0.12)",
         inputs=[_i("style", "textarea", "Стиль", required=True, default="upbeat pop with warm synths, 100 bpm"),
                 _i("lyrics", "textarea", "Текст песни (пусто = без слов)"),
                 _i("seconds", "slider", "Длина, секунд", default=60, min=10, max=240, step=5), SEED]),
    _app("sfx", "Звуковые эффекты (Stable Audio 3)", "audio",
         "Шумы, атмосфера, короткие звуки.", freefield="sfx", models="Stable Audio 3 Small SFX", status="ready",
         endpoint="fal-ai/stable-audio-3/small/sfx/text-to-audio", build=_sfx, output="audio",
         price="$0.0206 за один звук",
         inputs=[_i("description", "textarea", "Какой звук (по-английски)", required=True,
                    default="heavy rain on a tin roof with distant thunder"),
                 _i("seconds", "slider", "Длина, секунд", default=10, min=1, max=60, step=1), SEED]),
]
APPS_BY_ID = {a["id"]: a for a in APPS}


class AppError(ValueError):
    pass


# ---------------------------------------------------------------- public API

def public_app(app: dict) -> dict:
    return {k: v for k, v in app.items() if k != "build"}


def catalog() -> dict:
    return {"checked": CHECKED, "sections": SECTIONS, "apps": [public_app(a) for a in APPS]}


FILE_TYPES = {"image": MAX_IMAGE_MB, "video": MAX_VIDEO_MB, "audio": MAX_AUDIO_MB}


def validate(app_id: str, values: dict, resolve_file: Callable[[str], Path]) -> dict:
    """Check every input against the app spec; returns clean values (files as Paths). Raises AppError (Russian)."""
    app = APPS_BY_ID.get(app_id)
    if not app:
        raise AppError(f"Нет такого приложения: {app_id}")
    if app["status"] != "ready":
        raise AppError(f"«{app['name']}» пока «скоро»: {app['needs']}")
    values = values if isinstance(values, dict) else {}
    clean = {}
    for spec in app["inputs"]:
        key, kind = spec["id"], spec["type"]
        raw = values.get(key)
        if raw in (None, "") and "default" in spec:
            raw = spec["default"]
        if raw in (None, ""):
            if spec["required"]:
                raise AppError(f"Заполните поле «{spec['label']}».")
            clean[key] = None
            continue
        if kind in FILE_TYPES:
            path = resolve_file(str(raw))
            mb = path.stat().st_size / (1024 * 1024)
            if mb > FILE_TYPES[kind]:
                raise AppError(f"Файл «{spec['label']}» больше {FILE_TYPES[kind]} МБ — сожмите или укоротите.")
            clean[key] = path
        elif kind == "select":
            options = spec.get("options") or []
            match = next((o for o in options if str(o) == str(raw)), None)
            if match is None:
                raise AppError(f"«{spec['label']}»: недопустимое значение {raw!r}.")
            clean[key] = match
        elif kind in ("slider", "number"):
            try:
                num = float(raw)
            except (TypeError, ValueError):
                raise AppError(f"«{spec['label']}» должно быть числом.") from None
            if ("min" in spec and num < spec["min"]) or ("max" in spec and num > spec["max"]):
                raise AppError(f"«{spec['label']}» должно быть от {spec.get('min')} до {spec.get('max')}.")
            clean[key] = num
        elif kind == "toggle":
            clean[key] = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
        elif kind == "seed":
            clean[key] = _seed(raw) if _seed(raw) is not None else -1
        else:  # text / textarea
            text = str(raw).strip()
            if spec["required"] and not text:
                raise AppError(f"Заполните поле «{spec['label']}».")
            clean[key] = text[:4000]
    return clean


def build_request(app_id: str, clean: dict, file_to_url: Callable[[Path], str]) -> tuple[str, dict]:
    app = APPS_BY_ID[app_id]
    return app["endpoint"], app["build"](clean, file_to_url)


def placeholder_url(path: Path) -> str:
    """Dry-run stand-in for a file: names it, never reads or uploads it."""
    return f"<файл {Path(path).name}, {Path(path).stat().st_size // 1024} КБ — уйдёт в fal при запуске>"


def dry_run(app_id: str, values: dict, resolve_file: Callable[[str], Path]) -> dict:
    """Builds the exact request that «Запустить» would send, without sending anything (free)."""
    clean = validate(app_id, values, resolve_file)
    endpoint, body = build_request(app_id, clean, placeholder_url)
    app = APPS_BY_ID[app_id]
    return {"app": app_id, "endpoint": endpoint, "url": f"https://queue.fal.run/{endpoint}", "body": body,
            "price": app["price"], "submitted": False}


def output_url(result: dict, kind: str) -> str | None:
    """Pull the media URL from a fal result: video/image/images[]/audio (dict or plain string)."""
    for key in {"video": ("video",), "image": ("image", "images"), "audio": ("audio", "audio_file")}.get(kind, ()) + (
            "video", "image", "images", "audio"):
        value = result.get(key)
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("url")
        if isinstance(value, str) and value:
            return value
    return None


# ---------------------------------------------------------------- runs (real submit; only from the explicit «Запустить» button)

_RUNS: dict[str, dict] = {}
_LOCK = threading.Lock()


def get_run(run_id: str) -> dict | None:
    with _LOCK:
        state = _RUNS.get(run_id)
        return dict(state) if state else None


def start_run(app_id: str, values: dict, *, resolve_file, file_to_url, client, out_dir: Path, download,
              url_for: Callable[[str], str], background: bool = True) -> dict:
    """Validate, build and submit to fal (PAID). ``client.call(model, body, cancelled)`` returns the result JSON."""
    clean = validate(app_id, values, resolve_file)
    app = APPS_BY_ID[app_id]
    run_id = uuid.uuid4().hex[:12]
    state = {"id": run_id, "app": app_id, "name": app["name"], "endpoint": app["endpoint"], "status": "running",
             "output": None, "error": None, "started": time.time()}
    with _LOCK:
        _RUNS[run_id] = state

    def work():
        try:
            endpoint, body = build_request(app_id, clean, file_to_url)
            result = client.call(endpoint, body)
            url = output_url(result, app["output"])
            if not url:
                raise AppError(f"fal {endpoint}: в ответе нет ссылки на результат ({list(result)})")
            ext = Path(url.split("?")[0]).suffix or {"image": ".png", "audio": ".mp3"}.get(app["output"], ".mp4")
            name = f"{time.strftime('%Y-%m-%d-%Hh%Mm%Ss')}_ff_{app_id}_{run_id}{ext}"
            download(url, Path(out_dir) / name)
            with _LOCK:
                state.update(status="done", output={"filename": name, "url": url_for(name), "kind": app["output"]})
        except Exception as error:  # surfaced to the UI as text
            with _LOCK:
                state.update(status="failed", error=str(error)[:500])

    if background:
        threading.Thread(target=work, name=f"freefield-{app_id}-{run_id}", daemon=True).start()
    else:
        work()
    return get_run(run_id)
