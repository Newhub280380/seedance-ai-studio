# Seedance AI Studio

Визуальная Pinterest-style библиотека промтов для Seedance (AI-видео): галерея карточек
с fashion, брендами, 3D-товарами, liquid/mosaic-эффектами, cinematic, anime, UGC, Reels
и мемами из фильмов.

Сайт — **один самодостаточный файл** `index.html` (HTML + CSS + JS внутри, без сборщика,
без внешних CDN и шрифтов). Открывается двойным кликом, работает офлайн.

**Содержимое:** 31 промт в 8 категориях, поиск, фильтры-чипы, модалка с полным промтом,
копирование в один клик, загрузка референс-фото, избранное в LocalStorage.

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

## Структура

```
seedance-ai-studio/
├── index.html                  # весь сайт: разметка, стили, данные, логика
├── README.md                   # этот файл
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
