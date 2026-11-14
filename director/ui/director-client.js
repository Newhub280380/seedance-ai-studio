(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DirectorClient = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function buildTaskBody(input) {
    if (!input || !input.capability) throw new Error("capability required");
    if (!input.clientRequestId) throw new Error("clientRequestId required");
    var body = {
      capability: input.capability,
      client_request_id: input.clientRequestId,
      confirm: Boolean(input.confirm)
    };
    [
      "provider", "model", "prompt", "aspect_ratio", "duration", "width", "height",
      "resolution", "ratio", "original_asset_id", "voice", "narration", "identity_mode",
      "prompt_expansion_mode", "video_model", "style_line"
    ].forEach(function (key) {
      if (input[key] !== undefined && input[key] !== "") body[key] = input[key];
    });
    if (input.referenceAssetIds && input.referenceAssetIds.length) body.reference_asset_ids = input.referenceAssetIds;
    if (input.cameraTrajectory) body.camera_trajectory = input.cameraTrajectory;
    if (input.scenes) body.scenes = input.scenes;
    return body;
  }

  function mount(document) {
    var button = document.getElementById("directorBtn");
    var panel = document.getElementById("directorPanel");
    if (!button || !panel) return;
    var state = { capabilities: [], selected: "", assetId: null, references: [], clientRequestId: null, task: null, payload: null, pollTimer: null };
    var MODE_KEY = "director_mode";
    function mode() {
      try { return window.localStorage.getItem(MODE_KEY) === "advanced" ? "advanced" : "simple"; } catch (e) { return "simple"; }
    }
    function setMode(value) {
      try { window.localStorage.setItem(MODE_KEY, value); } catch (e) { /* private mode */ }
      stopPolling();
      if (value === "advanced") render(state.payload || {}); else renderSimple();
    }
    button.addEventListener("click", function () {
      setOpen(true);
      load();
    });
    document.addEventListener("click", function (event) {
      if (event.target.closest && event.target.closest("[data-cat]")) setOpen(false);
    });

    function setOpen(open) {
      panel.classList.toggle("hidden", !open);
      ["hero", "chips", "stats", "gallery"].forEach(function (id) {
        var node = document.getElementById(id);
        if (node) node.classList.toggle("hidden", open);
      });
      button.classList.toggle("active", open);
    }

    function load() {
      panel.innerHTML = "<p class='director-note'>Читаю возможности локального сервера…</p>";
      fetch("/api/director/capabilities").then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      }).then(function (payload) {
        state.capabilities = payload.capabilities || [];
        state.payload = payload;
        if (mode() === "advanced") render(payload); else renderSimple();
      }).catch(function () {
        panel.innerHTML = "<h2>Режиссёр</h2><p class='director-note'>Локальный сервер не запущен. Галерея промтов работает как раньше. Запуск: <code>python -m director</code> из корня репозитория, затем открыть эту страницу с того же сервера. Ключи только в окружении сервера.</p>";
      });
    }

    function render(payload) {
      var options = state.capabilities.map(function (item) {
        var status = (item.readiness && item.readiness.status) || "unknown";
        return "<option value='" + escapeAttr(item.id) + "'" + (item.id === state.selected ? " selected" : "") + ">" + escapeText(item.title) + " — " + escapeText(status) + "</option>";
      }).join("");
      panel.innerHTML = ""
        + "<h2>Режиссёр <button type='button' class='ghost director-mode' id='directorSimpleMode'>Простой режим</button></h2>"
        + "<p class='director-note'>Сервер не создаёт медиа сам. Отправка провайдеру начинается только после подтверждения и только если на сервере есть ключ. Снимок каталога: " + escapeText((payload.catalog && payload.catalog.fetched_at) || "нет") + ". Полный каталог в интерфейс не загружен.</p>"
        + "<label>Функция<select id='directorCapability'>" + options + "</select></label>"
        + "<div id='directorReadiness' class='director-note'></div>"
        + "<label>Промт<textarea id='directorPrompt' rows='4'></textarea></label>"
        + "<div class='director-row'>"
        + "<label>Модель<input id='directorModel' /></label>"
        + "<label>Соотношение<input id='directorAspect' placeholder='1:1 или 16:9' /></label>"
        + "<label>Длительность<input id='directorDuration' placeholder='5s или 5' /></label>"
        + "<label>Разрешение<input id='directorResolution' placeholder='720p или 768P' /></label>"
        + "</div>"
        + "<label class='director-check'><input id='directorConfirm' type='checkbox' /> Подтвердить отправку провайдеру. Это может списать деньги. Повтор той же заявки не создаёт вторую.</label>"
        + "<div class='director-row'>"
        + "<button type='button' class='ghost' id='directorFixture'>Синтетический образец</button>"
        + "<button type='button' class='primary' id='directorSubmit'>Сохранить задачу</button>"
        + "<button type='button' class='ghost' id='directorPoll'>Опросить</button>"
        + "<button type='button' class='ghost' id='directorCancel'>Отменить локально</button>"
        + "</div>"
        + "<pre id='directorLog' class='director-log'>Задачи появятся здесь. Идентификатор провайдера сохраняется в задаче.</pre>";
      document.getElementById("directorCapability").addEventListener("change", function () {
        var model = document.getElementById("directorModel");
        if (model) model.value = "";
        state.clientRequestId = null;
        showSelected();
      });
      document.getElementById("directorSimpleMode").addEventListener("click", function () { setMode("simple"); });
      document.getElementById("directorFixture").addEventListener("click", addFixture);
      document.getElementById("directorSubmit").addEventListener("click", submit);
      document.getElementById("directorPoll").addEventListener("click", function () { act("poll"); });
      document.getElementById("directorCancel").addEventListener("click", function () { act("cancel"); });
      showSelected();
    }

    // Простой режим для клиента: одна фраза (и при желании своё фото) -> ролик 5 с на Wan 2.2.
    // Всё техническое (модели, JSON, опрос) спрятано; расширенный режим остаётся по кнопке.
    function renderSimple() {
      var wan = null;
      for (var i = 0; i < state.capabilities.length; i++) {
        if (state.capabilities[i].id === "video.fal_wan") wan = state.capabilities[i];
      }
      var ready = wan && wan.readiness && wan.readiness.status === "ready_to_confirm";
      state.assetId = null;
      state.task = null;
      state.clientRequestId = null;
      panel.innerHTML = ""
        + "<h2>Режиссёр <button type='button' class='ghost director-mode' id='directorAdvancedMode'>Расширенный режим</button></h2>"
        + "<p class='director-note'>Опишите идею одной фразой — получится видео на 5 секунд (модель Wan 2.2). "
        + "Можно добавить своё фото: оно оживёт по вашему описанию.</p>"
        + (ready ? "" : "<p class='director-note' id='simpleNotReady'><b>Видео пока недоступно:</b> на сервере не задан ключ fal (FAL_KEY).</p>")
        + "<label>Что снять<textarea id='simplePrompt' rows='3' placeholder='Например: девушка в красном платье идёт по осеннему парку, лёгкий ветер'></textarea></label>"
        + "<div class='director-row'>"
        + "<label>Своё фото (необязательно)<input id='simplePhoto' type='file' accept='image/png,image/jpeg,image/webp' /></label>"
        + "<label>Формат<select id='simpleAspect'><option value='9:16'>Вертикально 9:16 (Reels, Stories)</option><option value='16:9'>Горизонтально 16:9</option><option value='1:1'>Квадрат 1:1</option></select></label>"
        + "</div>"
        + "<label class='director-check'><input id='simpleConfirm' type='checkbox' /> Согласен: один ролик стоит около $0.40 (оплата сервиса fal).</label>"
        + "<div class='director-row'><button type='button' class='primary' id='simpleGo'" + (ready ? "" : " disabled") + ">Сделать видео</button></div>"
        + "<p id='simpleStatus' class='director-note' role='status'></p>"
        + "<div id='simpleResult'></div>";
      document.getElementById("directorAdvancedMode").addEventListener("click", function () { setMode("advanced"); });
      document.getElementById("simplePhoto").addEventListener("change", uploadPhoto);
      document.getElementById("simpleGo").addEventListener("click", simpleSubmit);
      ["simplePrompt", "simpleAspect"].forEach(function (id) {
        document.getElementById(id).addEventListener("change", function () { state.clientRequestId = null; });
      });
    }

    function simpleStatus(text) {
      var node = document.getElementById("simpleStatus");
      if (node) node.textContent = text;
    }

    function uploadPhoto(event) {
      var file = event.target.files && event.target.files[0];
      state.assetId = null;
      state.clientRequestId = null;
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        var base64 = String(reader.result).split(",")[1] || "";
        simpleStatus("Загружаю фото…");
        postJson("/api/director/assets", { content_base64: base64, media_type: file.type || "image/jpeg" }).then(function (payload) {
          if (payload.ok === false) { simpleStatus("Фото не принято: " + ((payload.error && payload.error.message) || "ошибка")); return; }
          state.assetId = payload.asset && payload.asset.id;
          simpleStatus("Фото загружено — ролик будет сделан по нему.");
        }).catch(function () { simpleStatus("Не удалось загрузить фото."); });
      };
      reader.readAsDataURL(file);
    }

    function simpleSubmit() {
      var prompt = document.getElementById("simplePrompt").value.trim();
      if (!prompt) { simpleStatus("Напишите, что снять."); return; }
      if (!document.getElementById("simpleConfirm").checked) { simpleStatus("Поставьте галочку согласия — без неё ролик не заказывается."); return; }
      if (!state.clientRequestId) state.clientRequestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now());
      var body = buildTaskBody({
        capability: "video.fal_wan",
        clientRequestId: state.clientRequestId,
        confirm: true,
        prompt: prompt,
        aspect_ratio: document.getElementById("simpleAspect").value,
        resolution: "720p",
        original_asset_id: state.assetId || undefined
      });
      document.getElementById("simpleGo").disabled = true;
      simpleStatus("Отправляю заказ…");
      postJson("/api/director/tasks", body).then(simpleTrack).catch(function () {
        simpleStatus("Сервер не ответил. Повторное нажатие не создаст второй заказ.");
        document.getElementById("simpleGo").disabled = false;
      });
    }

    var SIMPLE_TEXT = {
      draft: "Заказ сохранён, но не отправлен.",
      submitting: "Отправляю заказ…",
      queued: "В очереди на генерацию…",
      processing: "Видео делается (обычно 1–3 минуты)…",
      completed: "Готово!"
    };

    function simpleTrack(payload) {
      var task = payload && payload.task;
      var go = document.getElementById("simpleGo");
      if (!task) {
        simpleStatus("Не принято: " + ((payload && payload.error && payload.error.message) || "ошибка"));
        if (go) go.disabled = false;
        return;
      }
      state.task = task;
      if (task.status === "completed") {
        stopPolling();
        simpleStatus(SIMPLE_TEXT.completed);
        var url = "/api/director/tasks/" + encodeURIComponent(task.id) + "/result";
        document.getElementById("simpleResult").innerHTML = "<video controls playsinline style='max-width:360px;width:100%;border-radius:14px' src='" + escapeAttr(url) + "'></video>"
          + "<p><a href='" + escapeAttr(url) + "' download='video-" + escapeAttr(task.id) + ".mp4'>Скачать видео</a></p>";
        state.clientRequestId = null;
        if (go) go.disabled = false;
        return;
      }
      if (["queued", "processing", "submitting"].indexOf(task.status) >= 0) {
        simpleStatus(SIMPLE_TEXT[task.status]);
        schedulePoll(task.id);
        return;
      }
      stopPolling();
      simpleStatus("Не получилось: " + ((task.error && (task.error.message || task.error.code)) || task.status) + ". Деньги за повтор той же заявки не спишутся.");
      if (go) go.disabled = false;
    }

    function schedulePoll(id) {
      stopPolling();
      state.pollTimer = window.setTimeout(function () {
        postJson("/api/director/tasks/" + encodeURIComponent(id) + "/poll", {}).then(simpleTrack).catch(function () { schedulePoll(id); });
      }, 5000);
    }

    function stopPolling() {
      if (state.pollTimer) window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }

    function selected() {
      var id = document.getElementById("directorCapability").value;
      state.selected = id;
      for (var i = 0; i < state.capabilities.length; i++) {
        if (state.capabilities[i].id === id) return state.capabilities[i];
      }
      return null;
    }

    function showSelected() {
      var item = selected();
      var node = document.getElementById("directorReadiness");
      if (!item || !node) return;
      var model = document.getElementById("directorModel");
      if (model && !model.value && item.default_model) model.value = item.default_model;
      var limits = "";
      var chosen = (item.models || []).filter(function (entry) { return entry.id === (model && model.value); })[0] || (item.models || [])[0];
      var constraints = chosen && chosen.constraints;
      if (constraints && constraints.aspectRatios) limits += " Допустимые соотношения: " + constraints.aspectRatios.join(", ") + ".";
      if (constraints && constraints.aspect_ratios && constraints.aspect_ratios.length) limits += " Допустимые соотношения: " + constraints.aspect_ratios.join(", ") + ".";
      if (constraints && constraints.durations) limits += " Длительности: " + constraints.durations.join(", ") + ".";
      if (item.resolutions) limits += " Разрешения: " + item.resolutions.join(", ") + ".";
      if (item.aspect_ratios) limits += " Допустимые соотношения: " + item.aspect_ratios.join(", ") + ".";
      if (item.durations) limits += " Длительности: " + item.durations.join(", ") + " с.";
      var ratios = (constraints && (constraints.aspectRatios || constraints.aspect_ratios)) || item.aspect_ratios || [];
      fillDefault("directorAspect", ratios, ratios.indexOf("9:16") >= 0 ? "9:16" : ratios[0]);
      var durations = (constraints && constraints.durations) || item.durations || [];
      fillDefault("directorDuration", durations, durations[0]);
      fillDefault("directorResolution", item.resolutions || [], (item.resolutions || [])[0]);
      node.textContent = "Готовность: " + ((item.readiness && item.readiness.status) || "unknown")
        + (item.reason ? ". " + item.reason : "")
        + (item.lane ? ". Контур: " + item.lane : "")
        + "."
        + limits
        + " Пиксельное сохранение лица не обещается.";
    }

    // Подставляет допустимое значение по умолчанию и список подсказок, чтобы пустое поле
    // не превращалось в отказ сервера (раньше «Сохранить задачу» с пустым соотношением давало ошибку).
    function fillDefault(id, values, fallback) {
      var input = document.getElementById(id);
      if (!input) return;
      var listId = id + "List";
      var list = document.getElementById(listId);
      if (!list) {
        list = document.createElement("datalist");
        list.id = listId;
        input.parentNode.appendChild(list);
        input.setAttribute("list", listId);
      }
      list.innerHTML = values.map(function (value) { return "<option value='" + escapeAttr(value) + "'></option>"; }).join("");
      if (values.length && values.map(String).indexOf(input.value.trim()) < 0) input.value = fallback == null ? "" : String(fallback);
      if (!values.length && input.dataset.autofilled === "1") input.value = "";
      input.dataset.autofilled = values.length ? "1" : "";
    }

    function addFixture() {
      fetch("/api/director/fixtures/solid", { method: "POST" }).then(function (response) { return response.json(); }).then(function (payload) {
        state.assetId = payload.asset && payload.asset.id;
        log("Синтетический образец " + state.assetId);
      }).catch(function (error) { log(String(error)); });
    }

    function submit() {
      var item = selected();
      if (!item) return;
      if (!state.clientRequestId) state.clientRequestId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now());
      var durationRaw = document.getElementById("directorDuration").value.trim();
      var duration = durationRaw === "" ? undefined : (/^\d+$/.test(durationRaw) ? Number(durationRaw) : durationRaw);
      var body = buildTaskBody({
        capability: item.id,
        provider: item.provider === "none" || item.provider === "comfy" ? item.provider : undefined,
        clientRequestId: state.clientRequestId,
        confirm: document.getElementById("directorConfirm").checked,
        model: document.getElementById("directorModel").value.trim(),
        prompt: document.getElementById("directorPrompt").value,
        aspect_ratio: document.getElementById("directorAspect").value.trim(),
        duration: duration,
        resolution: document.getElementById("directorResolution").value.trim(),
        original_asset_id: state.assetId || undefined,
        prompt_expansion_mode: item.id === "video.h3_max_hosted" ? "balanced" : undefined,
        ratio: item.id === "video.h3_max_hosted" ? (document.getElementById("directorAspect").value.trim() || "16:9") : undefined
      });
      postJson("/api/director/tasks", body).then(remember).catch(function (error) { log(String(error)); });
    }

    function act(name) {
      if (!state.task) { log("Нет задачи"); return; }
      postJson("/api/director/tasks/" + state.task.id + "/" + name, name === "confirm" ? {} : {}).then(remember);
    }

    function remember(payload) {
      if (payload.task) state.task = payload.task;
      var head = "";
      if (payload && payload.ok === false && payload.error) {
        head = "Не принято: " + (payload.error.message || payload.error.code) + "\n\n";
      } else if (payload && payload.task) {
        head = "Задача " + payload.task.id + ": " + payload.task.status + (payload.task.error ? " (" + (payload.task.error.message || payload.task.error.code) + ")" : "") + "\n\n";
      }
      log(head + JSON.stringify(payload, null, 2));
    }

    function postJson(url, body) {
      return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(function (response) {
        return response.json();
      });
    }

    function log(text) {
      var node = document.getElementById("directorLog");
      if (node) node.textContent = text;
    }

    function escapeText(value) {
      return String(value == null ? "" : value).replace(/[&<>]/g, function (char) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char];
      });
    }

    function escapeAttr(value) {
      return escapeText(value).replace(/'/g, "&#39;");
    }
  }

  return { buildTaskBody: buildTaskBody, mount: mount };
});
