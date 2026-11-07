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
    var state = { capabilities: [], selected: "", assetId: null, references: [], clientRequestId: null, task: null };
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
        render(payload);
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
        + "<h2>Режиссёр</h2>"
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
      document.getElementById("directorCapability").addEventListener("change", showSelected);
      document.getElementById("directorFixture").addEventListener("click", addFixture);
      document.getElementById("directorSubmit").addEventListener("click", submit);
      document.getElementById("directorPoll").addEventListener("click", function () { act("poll"); });
      document.getElementById("directorCancel").addEventListener("click", function () { act("cancel"); });
      showSelected();
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
      node.textContent = "Готовность: " + ((item.readiness && item.readiness.status) || "unknown")
        + (item.reason ? ". " + item.reason : "")
        + (item.lane ? ". Контур: " + item.lane : "")
        + "."
        + limits
        + " Пиксельное сохранение лица не обещается.";
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
      log(JSON.stringify(payload, null, 2));
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
