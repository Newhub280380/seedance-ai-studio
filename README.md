# Seedance AI Studio

Визуальная Pinterest-style библиотека промтов для Seedance (AI-видео): галерея карточек
с fashion, брендами, 3D-товарами, liquid/mosaic-эффектами, cinematic, anime, UGC, Reels
и мемами из фильмов.

Сайт — **один самодостаточный файл** `index.html` (HTML + CSS + JS внутри, без сборщика,
без внешних CDN и шрифтов). Открывается двойным кликом, работает офлайн.

**Содержимое:** 31 промт в 8 категориях, поиск, фильтры-чипы, модалка с полным промтом,
копирование в один клик, загрузка референс-фото, избранное в LocalStorage.

## Accio: «Народный режиссёр»

В этом репозитории рядом две вещи: живое приложение Seedance (`index.html`) и пакет
документов для Accio — [`docs/accio-director/`](docs/accio-director/).

Пакет — готовое задание для Accio Work: спроектировать приложение, где человек
загружает несколько семейных фото, нажимает одну кнопку и получает короткий ролик.
Внутри полный промпт (`ACCIO_PROMPT.md`), честные границы (`HONEST_SCOPE.md`:
ролик 15–30 с из 2–5 фото — реально; «кино на 2–3 минуты за один проход» — нет),
источники контекста, заметки по Open Design и библиотека `director_recipes/`
(роутер интентов и 212 YAML-рецептов). Вложения — в `attach/` (архив конвейера
`studio` и мета рецептов). Сайт `index.html` от пакета не зависит.

This repo is the Seedance studio and the Accio pack docs. The pack in
[`docs/accio-director/`](docs/accio-director/) is one assignment for Accio Work:
design an app where a person uploads a few family photos, presses one button, and
gets a short film. It includes the full prompt, the honest scope note, context
sources, Open Design notes, the `director_recipes/` library, and `attach/`.
`index.html` is unchanged and does not depend on the pack.

---

## Как открыть локально

```bash
git clone https://github.com/Newhub280380/seedance-ai-studio.git
cd seedance-ai-studio
# просто откройте файл — сервер не нужен
start index.html        # Windows
xdg-open index.html    # Linux
open index.html        # macOS
```

Либо запустить статический сервер (нужен, если захотите проверять вёрстку в разных
браузерах или подключить Pages-preview):

```bash
python -m http.server 8080
# открыть http://127.0.0.1:8080/
```

## Режиссёр: вызов провайдеров из этого сайта

Галерея промтов не изменилась. Кнопка «Режиссёр» в том же `index.html` говорит с локальным процессом:

```bash
python -m director
# http://127.0.0.1:8765/
```

Ключи `VENICE_API_KEY` и `MINIMAX_API_KEY` читаются только из окружения сервера и в страницу не попадают. Без ключа задача сохраняется, но запрос провайдеру не уходит. Подтверждение отдельно от опроса: повтор той же `client_request_id` не создаёт вторую заявку. Comfy JSON и оригинальные веса H3 в этом процессе не запускаются.

Проверка без сети и без генерации: `python -m unittest discover -s tests -v`. Ответы провайдеров в этих тестах подменяются.

## Товар: вырез и длинная сторона 3840

Существующая команда `prep_photo` вызывается процессом Режиссёра, не отдельным генератором. Веса на эту машину не входят. На box:

```bash
export DIRECTOR_PREP_PHOTO=/workspace/tools/photo-prep/prep_photo
python -m director
```

`PREP_ONNX_MODEL` читает сам `prep_photo`. Если запускается копия скрипта не из `/workspace/tools/photo-prep`, укажите существующий файл модели тем же переменным, не прописывая путь в коде.

Затем один раз, без повтора:

```bash
# original_asset_id — sha256 ответа POST /api/director/assets
curl -sS -X POST http://127.0.0.1:8765/api/director/tasks \
  -H 'Content-Type: application/json' \
  -d '{"capability":"image.product_prep","client_request_id":"zishel-prep-1","confirm":true,"original_asset_id":"<sha256>"}'
```

Режиссёр сам вызывает две готовые команды существующего скрипта. Сначала вырез `--upscale none --size 0`. По альфе больше 16, с запасом в 1 пиксель, из выреза берётся плотная рамка объекта. Затем тот же скрипт с `--already-cut --upscale onnx --size 3840 --denoise none --bg transparent --model u2net` увеличивает уже этот объект, а не холст с полями. Нативный вырез не перезаписывается и не прогоняется через апскейл второй раз. `completed` только если итог — PNG с альфой, длинная сторона холста 3840, пропорции объекта сохранены, а длинная сторона самого объекта не меньше 98% от 3840. Иначе `object_below_target` или `onnx_fallback`, без ложного успеха.

`GET /api/director/tasks/<id>/result` для этой операции отдаёт итог. Нативный вырез: `?role=cutout` или `?index=0`. Плотная рамка до апскейла: `?role=tight`.

Real-ESRGAN здесь только увеличивает уже вырезанный объект. Он не смягчает морщины, не отбеливает зубы и не делает косметическую ретушь.

## Человек или объект, затем отдельная правка Studio

`image.subject_prep` — тот же `prep_photo` и та же модель `u2net` для `person`, `object` и `generic`. Отдельной модели лица нет. Лицо по загруженным файлам не угадывается. В репозитории нет портрета оператора, и публичный кадр сравнения Comfy не подставляется ни примером, ни значением по умолчанию.

Подготовка не принимает промпт. Прозрачный PNG — мастер. JPEG на белом — тот же PNG без второго апскейла. Оригинал по sha256 не меняется. `upscale_backend` бывает `onnx`, `soft_fallback` или `skipped`. Мягкий запасной ресайз — это ошибка `onnx_fallback`, не успешный нейросетевой прогон. `neural_weights_observed` остаётся false: процесс не смотрит внутрь сессии ONNX.

Косметическая правка — другая задача, `studio.image_edit`. Нужны файл референса и явный `prompt`. Пример промпта есть в `python -m director.preflight`. Пока его нет в теле задачи, он не отправляется. Результат правки — новый файл. PNG и JPEG подготовки не перезаписываются. Это не медицинский протокол: доз, карусели «до/после» и клинических полей нет.

Правка картинки не требует MiniMax H3. Видео по референсу (`studio.reference_video`) и замена персонажа — отдельные необязательные маршруты, им нужен Ref2VA.

Сухой прогон, без задач и без весов:

```bash
python -m director.preflight
```

`POST /api/director/assets` принимает только JSON. Сырой `image/jpeg` отвечает `bad_json`.

```json
{"media_type":"image/jpeg","content_base64":"<стандартный base64 байтов файла>"}
```

Ответ: `asset.id` и `asset.sha256` — sha256 этих байтов. Повтор тех же байтов возвращает тот же id. Пустое тело, не-картинка и размер больше 2000000 байт отвергаются. Лимита частоты нет. Дериватив 4K, который не влезает в этот предел, остаётся файлом результата и передаётся в Studio по `prep_task_id`, а не повторной заливкой в assets.

```bash
curl -sS -X POST http://127.0.0.1:8765/api/director/tasks \
  -H 'Content-Type: application/json' \
  -d '{"capability":"image.subject_prep","client_request_id":"portrait-prep-1","confirm":true,"subject":"person","original_asset_id":"<sha256 своего портрета>"}'
```

Промпт ретуши подставляется только руками, в отдельной задаче. `<prep-id>` — id завершённой подготовки. Пока Studio не отдал файл и Director его не проверил, статус не `completed`.

```bash
curl -sS -X POST http://127.0.0.1:8765/api/director/tasks \
  -H 'Content-Type: application/json' \
  -d '{"capability":"studio.image_edit","client_request_id":"portrait-edit-1","confirm":true,"prep_task_id":"<prep-id>","reference_role":"jpeg_white","image_model":"qwen","prompt":"<явный промпт из preflight>"}'
curl -sS -X POST http://127.0.0.1:8765/api/director/tasks/<edit-id>/poll -H 'Content-Type: application/json' -d '{}'
```

Модель правки по умолчанию — Qwen-Image-2.1-Turbo (`image_model` `qwen`), скрипт Studio `scripts/edit.sh`. H3 для неё не нужен. Необязательные модели того же маршрута: `qwen-full` (`--image-edit`), `flux3k-fast` и `flux3k-full` (`--flux3`), `moss-image` (`--moss`). Видео по `<Picture 1>` требует MiniMax H3 Ref2VA (`oneshot-setup.sh --ref2va` или `oneshot-setup-spark.sh --ref2va` на CUDA) и не заменяет правку картинки.

На box для повторного прогона нужен скрипт из этого дерева, потому что у исходного `prep_photo` нет `--already-cut`, и существующий файл ONNX:

```bash
export DIRECTOR_PREP_PHOTO="$PWD/director/vendor/photo-prep/prep_photo"
export PREP_ONNX_MODEL=/workspace/tools/realesrgan/realesrgan_x4plus_fp16.onnx
python -m director
```

## LTX-2.5 через Режиссёр

Официальные модули [Lightricks/LTX-2](https://github.com/Lightricks/LTX-2) на коммите `4f521619ae0029e8fbb64d555030c2b121a33b4f`. README рекомендует LTX-2.5, не 2.3. Код Apache-2.0. Веса отдельно, LTX-2.x Community License. Режиссёр их не скачивает.

- `ltx.distilled` — текст или кадр `--image`. Исходное видео сюда не принимается.
- `ltx.dfr` — то же семейство плюс detailing IC-LoRA. Это не правка чужого ролика.
- `ltx.ic_lora` — видео по референс-видео `--video-conditioning`, свой IC-LoRA, по желанию маска и ключевые кадры. Это не замена товара как у Parrot и не блокировка текста упаковки.
- `ltx.retake` — переснять интервал времени.

Без CUDA, без пакета `ltx_pipelines` и без файлов в `LTX_MODEL_ROOT` ответ `ltx_not_ready`, MP4 не создаётся. `--quantization fp8-cast` и `--offload cpu|disk` уходят в официальный CLI только если их попросили. Минимальный VRAM в README не указан.

Платный многопользовательский режим выключен. Attachment A(20) запрещает конкурирующий сервис без отдельной лицензии и не снимается порогом выручки в 10 млн долларов. Это не юридическое заключение. Реклама и подписки отсюда не запускаются.

```bash
git clone https://github.com/Lightricks/LTX-2.git
git -C LTX-2 checkout 4f521619ae0029e8fbb64d555030c2b121a33b4f
export LTX_PYTHON=python3
export LTX_MODEL_ROOT=/path/to/models/ltx-2.5
python -m director
```

## Imagination Studio рядом с Режиссёром

Это отдельное приложение [Imagination Studio by keys+Wasserman](https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman) тега `v4.3.1`, коммит `3926471d59bfe8a51a295b7bfdef6cda0b09e1de`. Режиссёр его не заменяет. Код Studio — Apache-2.0. Веса и чужие модели имеют свои лицензии, включая территориальные ограничения MiniMax H3. «Полностью открыто» отсюда не следует.

`bash oneshot-setup.sh` на Linux сразу уходит в CUDA-установщик и качает десятки или сотни гигабайт. На машине без `nvidia-smi` он останавливается до загрузки. Его здесь не запускали дальше этой проверки.

Приложение без весов, закреплённая версия, без автообновления:

```bash
git clone --depth 1 --branch v4.3.1 https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install 'fastapi==0.142.2' 'uvicorn==0.54.0' 'python-multipart==0.0.32' 'pillow==12.3.0' 'huggingface_hub==1.33.0'
# в STUDIO_HOME заранее: .studio-agents/settings.json с "default": "local"
export STUDIO_HOME=...
export STUDIO_DEFAULT_BRAIN=local
export PREFIX=...
export HOST=127.0.0.1 PORT=7870
export STUDIO_PIDFILE=/tmp/imagination-studio.pid
bash scripts/app.sh
```

`STUDIO_PIDFILE` — штатная ветка `scripts/app.sh`: она запускает `app/server.py` и не включает лаунчер, который сам обновляет дерево. Мозг по умолчанию только `local`. Облачные Claude и Codex в опросе этой машины не найдены, запасной отправки кадра им нет.

Character Swap и Remove a person без весов отвечают `ready: false`. Remove a person — это дорисовка видео, не прозрачный вырез. Вырез товара остаётся `image.product_prep`, вырез человека или объекта — `image.subject_prep`. Правка по файлу — `studio.image_edit` (`POST /api/jobs`, kind `edit`); она не идёт в H3. `POST /api/slate/direct` в коде есть и из Режиссёра не вызывается: он стартует собственного режиссёра Studio. Контрактные тесты подменяют Studio и не являются прогоном на GPU.

## Структура

```
seedance-ai-studio/
├── index.html                  # весь сайт: разметка, стили, данные, логика
├── README.md                   # этот файл
├── docs/accio-director/        # пакет Accio «Народный режиссёр» (документы, не сайт)
│   ├── README.md
│   ├── ACCIO_PROMPT.md
│   ├── CONTEXT_SOURCES.md
│   ├── HONEST_SCOPE.md
│   ├── open_design_notes.md
│   ├── attach/
│   └── director_recipes/
└── .github/
    ├── scripts/
    │   └── validate_site.py    # валидатор сайта (Python, без зависимостей)
    └── workflows/
        ├── ci.yml              # CI: валидация + smoke-тест отдачи по HTTP
        └── deploy-pages.yml    # ручной деплой в GitHub Pages
```

`index.html` устроен так:

| Часть | Что внутри |
|-------|-----------|
| `<style>` | тема, сетка Pinterest-галереи, модалка, адаптив |
| `<script>` | массив `categories` (id / подпись / иконка) и массив `items` (промты) |
| разметка | `nav`, `hero`, `aside` (категории + поиск), `section.gallery`, модалка |

## Как добавить промт

Всё в одном месте — массив `items` в `index.html`:

```js
{
  cat: 'fashion',            // id из массива categories
  title: 'Chrome Runway',
  desc: 'Короткое описание карточки.',
  tag: 'FASHION',            // подпись на карточке
  size: 'tall',              // tall | wide | normal — влияет на пропорции в сетке
  c1: '#ef4f9b', c2: '#24134d',  // цвета градиента обложки
  label: 'CHROME\nRUNWAY',   // крупная надпись на обложке
  prompt: 'Полный текст промта для копирования.'
}
```

Новую категорию добавляем в `categories` — id должен совпадать с `cat` в карточках.
После правки прогоните валидатор (ниже) — CI не даст залить сломанную страницу.

## CI

`.github/workflows/ci.yml` запускается на каждый push в `main` и на каждый PR:

1. **Валидация** — `python .github/scripts/validate_site.py`:
   - наличие обязательных файлов (`index.html`, `README.md`);
   - корректность HTML: парность тегов, `<meta charset>`, `<meta viewport>`,
     непустой `<title>`, `lang` у `<html>`, отсутствие дублей `id`;
   - все локальные ссылки (`href`/`src`) указывают на существующие файлы;
   - все `id`, к которым обращается inline-JS, реально есть в разметке;
   - синтаксис inline-JS проверяется через `node --check` (если `node` доступен).
2. **Smoke-тест** — сайт поднимается локальным HTTP-сервером, `index.html` должен
   отдаться с кодом `200` и содержать `<title>`.

Локально то же самое:

```bash
python .github/scripts/validate_site.py   # exit code 0 = всё ок
```

Скрипт не тянет зависимости и не ходит в сеть — проверки быстрые и воспроизводимые.

## Деплой

**Вариант 1 — GitHub Pages из ветки (самый простой, без CI):**
Settings → Pages → Build and deployment → Source: *Deploy from a branch* → ветка `main`,
папка `/ (root)`. Сайт — один файл, сборка не требуется.

**Вариант 2 — автоматический деплой (workflow вручную):**
1. Settings → Pages → Source: *GitHub Actions* (включает Pages).
2. Actions → *Deploy to GitHub Pages* → Run workflow.
Workflow собирает `_site/` (`index.html` + `README.md`) и публикует через
`actions/deploy-pages`. Запускается только вручную (`workflow_dispatch`), чтобы
не публиковать репозиторий случайно.

## Заметка

В истории прогонов есть красный run **«Copilot cloud agent»** (ветка
`copilot/create-pinterest-style-interface`). Это не CI проекта: workflow генерируется
самим GitHub Copilot coding agent, и он упал на стороне GitHub —
`CAPIError: 400 Error while downloading file. Upstream status code: 404`, то есть на
ошибке загрузки модели/файла во время выполнения агента, а не на коде сайта.
Свой workflow он не заменяет, поэтому он оставлен как есть, а рабочая проверка
добавлена отдельным `ci.yml`.

## Лицензия / данные

Промты — авторский текст проекта, можно свободно использовать и переносить.
Внешние сервисы (Seedance и т.п.) для генерации видео не требуются: сайт только
хранит и отдаёт текст промтов.
