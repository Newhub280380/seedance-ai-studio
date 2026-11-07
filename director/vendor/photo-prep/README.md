## Главное
- **Основной вход — фото из WhatsApp** (пережатые, 900×1600-ish, часто с сеткой/блоками). Дефолт `--denoise whatsapp` убирает это до апскейла.
- **На box без GPU дефолт = `--upscale onnx`** (Real-ESRGAN x4plus, модель `../realesrgan/realesrgan_x4plus_fp16.onnx`, ~2 мин на 900×1600). Если модели нет — fallback на soft с предупреждением в stderr.
- **На локалке с Vulkan** можно `--upscale realesrgan` (ncnn-vulkan, быстрее).
- Порядок: denoise → rembg → ×4 → resize/format.

# prep_photo — одна команда для фото перед нейросетью

Одно товарное фото прогоняется **одним скриптом** через три шага подряд:

1. **Вырез фона** — `rembg` (по умолчанию модель `u2net`; можно `birefnet-general`)
2. **Чёткость** — мягкий CPU-апскейл (LANCZOS ×2 + лёгкий unsharp) или Real-ESRGAN (Vulkan), если бинарь работает
3. **Нормализация** — длинная сторона, PNG/JPEG, белый или прозрачный фон

Итог: готовый файл рядом с исходником (`имя_prep.png`) или в папке `out/`, если она есть.

## Быстрый старт (на этом box)

```bash
/workspace/tools/photo-prep/prep_photo demo/in.png
# или с флагами:
/workspace/tools/photo-prep/prep_photo demo/in.png --size 2048 --format png --bg white --model u2net
```

Полезные флаги:

| Флаг | Смысл |
|------|--------|
| `--size 2048` | длинная сторона в пикселях |
| `--format png\|jpg` | формат выхода |
| `--bg white\|transparent` | фон после выреза |
| `--model u2net` | быстрее, легче |
| `--model birefnet-general` | точнее, модель тяжелее |
| `--upscale onnx` | Real-ESRGAN x4plus ×4 через onnxruntime CPU (**по умолчанию**) |
| `--upscale soft` | LANCZOS ×2 + unsharp (запасной, быстрый) |
| `--upscale realesrgan` | Real-ESRGAN ncnn-vulkan |
| `--no-upscale` | без апскейла |
| `--denoise whatsapp\|none` | предочистка сжатия/сетки WhatsApp (по умолчанию whatsapp) |
| `--tile N` / `--tile-pad N` | тайлы ONNX (дефолт 192/16; меньше = меньше RAM) |

Пути на box:

- скрипт: `/workspace/tools/photo-prep/prep_photo`
- Real-ESRGAN: `/workspace/tools/realesrgan/realesrgan-ncnn-vulkan` + `models/`

> На этом сервере **нет NVIDIA GPU**, Vulkan не поднимается (`vkCreateInstance failed`).  
> Поэтому `--upscale realesrgan` падает и скрипт сам уходит на `--upscale soft`.  
> На локалке с нормальным GPU/драйверами Vulkan обычно работает.

## Как поставить то же на Windows / локалку

Коротко — нужны три вещи + этот скрипт.

### 1) Python + rembg

```bash
python -m pip install "rembg[cli]" pillow
# если CLI ругается на filetype / watchdog / aiohttp — доставьте их тем же pip
rembg i --help
```

### 2) Real-ESRGAN (по желанию, для жёсткого апскейла)

Скачайте релиз с моделями:

- Linux: [realesrgan-ncnn-vulkan-20220424-ubuntu.zip](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip)
- Windows: [realesrgan-ncnn-vulkan-20220424-windows.zip](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip)

Распакуйте так, чтобы рядом со скриптом была структура:

```
tools/
  realesrgan/
    realesrgan-ncnn-vulkan(.exe)
    models/
  photo-prep/
    prep_photo.py
    prep_photo      # на Windows: prep_photo.bat → python prep_photo.py %*
```

Нужен рабочий Vulkan (драйвер видеокарты). Без него используйте `--upscale soft`.

### 3) Скрипт

Скопируйте папку `photo-prep` как есть. Запуск:

```bash
# Linux / macOS
./prep_photo photo.jpg --size 2048 --format png --bg white

# Windows (из cmd/PowerShell в папке photo-prep)
python prep_photo.py photo.jpg --size 2048 --format png --bg white --upscale realesrgan
```

## Пример для Beautyart

```bash
prep_photo bottle.jpg --size 2048 --format png --bg white --model u2net
# → bottle_prep.png рядом с исходником
```

Для более чистого контура (если машина тянет скачивание модели):

```bash
prep_photo bottle.jpg --model birefnet-general --bg white --format png
```

## Smoke-тест

В `demo/` лежит синтетическое «товарное» фото и результат прогона:

```bash
cd /workspace/tools/photo-prep
./prep_photo demo/in.png -v
```
