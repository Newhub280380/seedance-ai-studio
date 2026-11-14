/* Freefield apps for Режиссёр 1.0: catalog, free request check, paid run on fal only after confirmation.
   Talks to /api/director/freefield/* (director/freefield_route.py). All 28 apps open an in-page form; «скоро» shows a card with a disabled run button. */
(function () {
  "use strict";
  var CSS = ".ff-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}" +
    ".ff-card{border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:13px;text-align:left;color:var(--text);font:inherit}" +
    "button.ff-card{cursor:pointer}button.ff-card:hover{border-color:var(--accent)}" +
    ".ff-card.soon{border-style:dashed;opacity:.8}.ff-card b{display:block;margin-bottom:4px}" +
    ".ff-card small{display:block;color:var(--muted);font-size:12px;margin-top:4px}.ff-need{color:#ffcf6e!important}" +
    ".ff-badge{float:right;font-size:11px;padding:2px 7px;border-radius:8px;background:#1d3b1d;color:var(--accent)}.soon .ff-badge{background:#262a37;color:var(--muted)}" +
    ".ff-form{display:grid;gap:12px;max-width:720px}.ff-form label{display:grid;gap:5px;color:var(--muted);font-size:13px}" +
    ".ff-form input[type=text],.ff-form input[type=number],.ff-form textarea,.ff-form select{background:#151720;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:9px}" +
    ".ff-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.ff-err{color:#ff7b7b}.ff-ok{color:var(--accent)}" +
    ".ff-pre{white-space:pre-wrap;background:#0d0f15;border:1px solid var(--line);border-radius:10px;padding:10px;font-size:12px;max-height:260px;overflow:auto}" +
    ".ff-section{margin:18px 0 8px;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}" +
    ".ff-out video,.ff-out img{max-width:100%;max-height:420px;border-radius:12px}";

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function api(method, url, body) {
    return fetch(url, { method: method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) {
            var err = new Error((data && data.error && data.error.message) || ("Ошибка " + r.status));
            err.status = r.status;
            throw err;
          }
          return data;
        });
      });
  }

  function mount() {
    var button = document.getElementById("freefieldBtn");
    var panel = document.getElementById("freefieldPanel");
    if (!button || !panel) return;
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    var catalog = null;
    var timer = null;

    function setOpen(open) {
      panel.classList.toggle("hidden", !open);
      button.classList.toggle("active", open);
      if (open) {
        ["hero", "chips", "stats", "gallery", "directorPanel"].forEach(function (id) {
          var node = document.getElementById(id);
          if (node) node.classList.add("hidden");
        });
        var dir = document.getElementById("directorBtn");
        if (dir) dir.classList.remove("active");
      } else if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    button.addEventListener("click", function () { setOpen(true); load(); });
    document.addEventListener("click", function (event) {
      var t = event.target;
      if (!t.closest) return;
      if (t.closest("#directorBtn") || t.closest("[data-cat]")) {
        setOpen(false);
        if (t.closest("[data-cat]")) {
          ["hero", "chips", "stats", "gallery"].forEach(function (id) {
            var node = document.getElementById(id);
            if (node) node.classList.remove("hidden");
          });
        }
      }
    });

    function load() {
      panel.innerHTML = "<p class='director-note'>Загружаю приложения…</p>";
      api("GET", "/api/director/freefield/apps").then(function (data) {
        catalog = data;
        renderCatalog();
      }).catch(function (e) {
        panel.innerHTML = "<h2>Приложения</h2><p class='ff-err'>" + (e.status === 404
          ? "Сервер Режиссёра 1.0 запущен со старой версией. Закройте его чёрное окно «Режиссёр 1.0» и откройте ярлык «Режиссёр 1» снова — приложения появятся."
          : "Сервер не ответил: " + esc(e.message)) + "</p>";
      });
    }

    function renderCatalog() {
      var html = "<h2>Приложения (как во Freefield)</h2><p class='director-note'>«работает» — запуск в облаке fal, цена на карточке; " +
        "«скоро» — откроется карточка с отключённым запуском.</p>";
      if (!catalog.fal_ready) html += "<p class='ff-err'>Ключа fal нет на сервере — запуск недоступен, проверка запроса работает.</p>";
      catalog.sections.forEach(function (section) {
        var apps = catalog.apps.filter(function (a) { return a.section === section.id; });
        apps.sort(function (a, b) { return (b.priority - a.priority) || ((b.status === "ready") - (a.status === "ready")); });
        if (!apps.length) return;
        html += "<div class='ff-section'>" + esc(section.label) + "</div><div class='ff-grid'>";
        apps.forEach(function (a) {
          var inner = "<span class='ff-badge'>" + (a.status === "ready" ? "работает" : "скоро") + "</span><b>" + esc(a.name) + "</b>" +
            esc(a.summary) + "<small>" + esc(a.models) + "</small>" +
            (a.status === "ready" ? "<small>" + esc(a.price) + "</small>" : "<small class='ff-need'>Нужно: " + esc(a.needs) + "</small>");
          html += "<button type='button' class='ff-card" + (a.status === "ready" ? "" : " soon") + "' data-ff-app='" + esc(a.id) + "'>" + inner + "</button>";
        });
        html += "</div>";
      });
      html += "<p class='director-note'>Цены — со страниц fal, проверено " + esc(catalog.checked) + ".</p>";
      panel.innerHTML = html;
      Array.prototype.forEach.call(panel.querySelectorAll("[data-ff-app]"), function (node) {
        node.addEventListener("click", function () { renderForm(node.getAttribute("data-ff-app")); });
      });
    }

    function field(input) {
      var label = esc(input.label) + (input.required ? " *" : "") + (input.help ? " <span style='opacity:.7'>— " + esc(input.help) + "</span>" : "");
      var id = "ff_" + input.id;
      var d = input.default == null ? "" : input.default;
      if (input.type === "image" || input.type === "video" || input.type === "audio") {
        var accept = { image: "image/*", video: "video/*", audio: "audio/*" }[input.type];
        return "<label>" + label + "<input type='file' accept='" + accept + "' data-ff-file='" + esc(input.id) + "'><small id='" + id + "_st'></small></label>";
      }
      if (input.type === "textarea") return "<label>" + label + "<textarea rows='3' id='" + id + "'>" + esc(d) + "</textarea></label>";
      if (input.type === "select") {
        return "<label>" + label + "<select id='" + id + "'>" + input.options.map(function (o) {
          return "<option value='" + esc(o) + "'" + (String(o) === String(d) ? " selected" : "") + ">" + esc(o) + "</option>";
        }).join("") + "</select></label>";
      }
      if (input.type === "toggle") return "<label class='ff-row'><input type='checkbox' id='" + id + "'" + (d ? " checked" : "") + "> " + esc(input.label) + "</label>";
      if (input.type === "slider") {
        return "<label>" + label + "<input type='range' id='" + id + "' min='" + input.min + "' max='" + input.max + "' step='" + (input.step || 1) + "' value='" + esc(d) + "'></label>";
      }
      if (input.type === "seed" || input.type === "number") return "<label>" + label + "<input type='number' id='" + id + "' value='" + esc(d) + "'></label>";
      return "<label>" + label + "<input type='text' id='" + id + "' value='" + esc(d) + "'></label>";
    }

    function renderForm(appId) {
      var app = catalog.apps.filter(function (a) { return a.id === appId; })[0];
      var soon = app.status !== "ready";
      var files = {};
      var title = "<h2>" + esc(app.name) + (soon ? " <span class='ff-badge'>скоро</span>" : "") + "</h2>";
      var note = soon
        ? esc(app.summary) + "<br>Модель: " + esc(app.models) + "<br>Нужно: " + esc(app.needs)
        : esc(app.summary) + "<br>Модель: " + esc(app.models) + "<br>Цена: " + esc(app.price);
      var actions = soon
        ? "<div class='ff-row'><button type='button' class='primary' id='ffRun' disabled title='Ещё не подключено'>Запустить — скоро</button></div>"
        : "<div class='ff-row'><button type='button' class='ghost' id='ffCheck'>Проверить запрос (бесплатно)</button>" +
          "<button type='button' class='primary' id='ffRun'" + (catalog.fal_ready ? "" : " disabled title='Нет ключа fal'") + ">Запустить (платно, fal)</button></div>";
      panel.innerHTML = "<p><button type='button' class='ghost' id='ffBack'>← Все приложения</button></p>" +
        title + "<p class='director-note'>" + note + "</p>" +
        "<div class='ff-form'>" + (soon ? "" : app.inputs.map(field).join("")) +
        actions +
        "<div id='ffMsg'></div><div id='ffOut' class='ff-out'></div></div>";
      document.getElementById("ffBack").addEventListener("click", function () {
        if (timer) { clearInterval(timer); timer = null; }
        renderCatalog();
      });
      if (soon) return;
      Array.prototype.forEach.call(panel.querySelectorAll("[data-ff-file]"), function (node) {
        node.addEventListener("change", function () {
          var key = node.getAttribute("data-ff-file");
          var file = node.files && node.files[0];
          var status = document.getElementById("ff_" + key + "_st");
          if (!file) return;
          status.textContent = "Загружаю…";
          var reader = new FileReader();
          reader.onload = function () {
            var base64 = String(reader.result).split(",")[1] || "";
            api("POST", "/api/director/freefield/uploads", { media_type: file.type, content_base64: base64 }).then(function (r) {
              files[key] = r.filename;
              status.textContent = file.name + " — загружено";
            }).catch(function (e) { delete files[key]; status.textContent = "Не загрузилось: " + e.message; });
          };
          reader.readAsDataURL(file);
        });
      });

      function values() {
        var out = {};
        app.inputs.forEach(function (input) {
          if (input.type === "image" || input.type === "video" || input.type === "audio") {
            if (files[input.id]) out[input.id] = files[input.id];
            return;
          }
          var node = document.getElementById("ff_" + input.id);
          if (!node) return;
          if (input.type === "toggle") out[input.id] = node.checked;
          else if (input.type === "slider" || input.type === "seed" || input.type === "number") out[input.id] = node.value === "" ? "" : Number(node.value);
          else out[input.id] = node.value;
        });
        return out;
      }
      var msg = document.getElementById("ffMsg");
      var outBox = document.getElementById("ffOut");
      document.getElementById("ffCheck").addEventListener("click", function () {
        msg.innerHTML = "Проверяю…";
        api("POST", "/api/director/freefield/apps/" + encodeURIComponent(app.id) + "/dry-run", { inputs: values() }).then(function (plan) {
          msg.innerHTML = "<div class='ff-pre'>Ничего не отправлено. Запрос, который уйдёт в fal:\nPOST " + esc(plan.url) + "\n" + esc(JSON.stringify(plan.body, null, 2)) + "</div>";
        }).catch(function (e) { msg.innerHTML = "<p class='ff-err'>" + esc(e.message) + "</p>"; });
      });
      document.getElementById("ffRun").addEventListener("click", function () {
        if (!window.confirm("Запустить «" + app.name + "» в облаке fal? Это платно: " + app.price + ".")) return;
        msg.innerHTML = "Отправляю…";
        api("POST", "/api/director/freefield/apps/" + encodeURIComponent(app.id) + "/run", { inputs: values(), confirm: true }).then(function (run) {
          track(run);
        }).catch(function (e) { msg.innerHTML = "<p class='ff-err'>" + esc(e.message) + "</p>"; });
      });

      function track(run) {
        if (timer) { clearInterval(timer); timer = null; }
        show(run);
        if (run.status !== "running") return;
        timer = setInterval(function () {
          api("GET", "/api/director/freefield/runs/" + encodeURIComponent(run.id)).then(function (state) {
            show(state);
            if (state.status !== "running") { clearInterval(timer); timer = null; }
          }).catch(function (e) { msg.innerHTML = "<p class='ff-err'>" + esc(e.message) + "</p>"; });
        }, 4000);
      }
      function show(run) {
        if (run.status === "running") { msg.innerHTML = "<p>Генерация в облаке…</p>"; return; }
        if (run.status === "failed") { msg.innerHTML = "<p class='ff-err'>Ошибка: " + esc(run.error) + "</p>"; return; }
        msg.innerHTML = "<p class='ff-ok'>Готово: " + esc(run.output.filename) + "</p>";
        var url = esc(run.output.url);
        outBox.innerHTML = (run.output.kind === "video" ? "<video src='" + url + "' controls></video>"
          : run.output.kind === "audio" ? "<audio src='" + url + "' controls></audio>" : "<img src='" + url + "' alt=''>") +
          "<p><a href='" + url + "' download>Скачать</a></p>";
      }
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
