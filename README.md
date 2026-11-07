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

На box для повторного прогона нужен скрипт из этого дерева, потому что у исходного `prep_photo` нет `--already-cut`, и существующий файл ONNX:

```bash
export DIRECTOR_PREP_PHOTO="$PWD/director/vendor/photo-prep/prep_photo"
export PREP_ONNX_MODEL=/workspace/tools/realesrgan/realesrgan_x4plus_fp16.onnx
python -m director
```

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
