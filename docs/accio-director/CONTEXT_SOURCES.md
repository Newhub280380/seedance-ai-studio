# Источники контекста (приложить к Accio по необходимости — не вставлять целиком в промпт)

## Код режиссёра (главное, прикладывать)
- `/workspace/studio/` — рабочий конвейер фото→видео (~1.8k строк Python): `studio.py`, `lib/clip_chain.py` (цепочка last-frame, дрейф лица, motion_reroll), `lib/stitch.py` (склейка, цвет стыков, звук, апскейл), `lib/photo_enhance.py` (фон/улучшение + QC), `lib/pipelines.py`, `lib/core.py` (бюджет), `models.yaml`, `config.yaml`, `README.md`, `ESTIMATES.md`. Без `cache/`, `out_test/`, `jobs/` с фото клиентов и без ключей.
- `/workspace/minivideo/` — пробные скрипты fal/Higgsfield (`fal_run.py`, `step1.py`, a1/a2/b1/b2.py) + примеры результатов.

## Рецепты и знания
- `/workspace/director_recipes/` — `router.md` (роутер интентов), `catalog.json`, `tools.md`, `duplicates.md`, `recipes/*.yaml` (212 шт.); архив `director_recipes.zip`.
- `/workspace/algorithms_found/78_director_mode_one_image_9_keyframes_kling26.md`; сырьё `algorithms_found/raw/tg/directorsoyuz.txt`, `directoreels.txt`.
- `/workspace/discord_new/schemes/08_claude_code_higgsfield_mcp_director_workflow.md`
- `/workspace/research/stayingahead/directors_bible_invideo_agent2.txt` (.pdf)
- `/workspace/research/falpages/minimax_h3-max_director.html`

## Инфраструктура
- `/workspace/research/film_studio_stack/REPORT.md` (+ `workflows_all.json`, `shtab_kinostudii_polny_stek.json`) — аудит сервера n8n+ffmpeg (DigitalOcean, без GPU). Перед отправкой убрать IP/секреты.
- `/workspace/eva_pipeline/` (step1_in, step1_nobg — пример удаления фона), `/workspace/eva_film/`, `/workspace/eva_remake/`.

## UI-референс
- `/workspace/beautyart-hq-run/beautyart-hq/` — `index.html`, `shared/panels.js`, `shared/agents.js` (панели видео/агентов HQ). Это внутренняя админка — как антипример для бабушки (там всё видно), полезна только как бэкенд-идеи.

## Не прикладывать
- `/workspace/secrets/`, `*key*.json/txt`, фото клиентов.
