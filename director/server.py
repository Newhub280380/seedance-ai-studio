import json
from urllib.parse import urlparse

from director.errors import DirectorRefusal
from director.service import DirectorApp
from director.transport import UrllibTransport


def build_app(repo_root, data_dir, venice_transport, minimax_transport, venice_api_key="", minimax_api_key=""):
    return DirectorApp(
        repo_root,
        data_dir,
        venice_transport,
        minimax_transport,
        venice_api_key=venice_api_key,
        minimax_api_key=minimax_api_key,
    )


class DirectorHandler:
    def __init__(self, app, repo_root):
        self.app = app
        self.repo_root = repo_root

    def handle(self, method, path, body, headers):
        parsed = urlparse(path)
        route = parsed.path
        if route.startswith("/api/director"):
            return self._api(method, route, body)
        if method != "GET":
            return 405, {"ok": False, "error": {"code": "method", "message": "GET only"}}, "application/json"
        return self._static(route)

    def _api(self, method, route, body):
        try:
            if method == "GET" and route == "/api/director/capabilities":
                return 200, self.app.capabilities(), "application/json"
            if method == "GET" and route == "/api/director/tasks":
                return 200, {"ok": True, "tasks": [_view(task) for task in self.app.store.list_tasks()]}, "application/json"
            if method == "POST" and route == "/api/director/fixtures/solid":
                return 200, {"ok": True, "asset": self.app.solid_fixture()}, "application/json"
            if method == "POST" and route == "/api/director/assets":
                payload = _json(body)
                import base64
                data = base64.b64decode(payload.get("content_base64") or "")
                asset = self.app.put_asset(data, payload.get("media_type") or "image/png")
                return 200, {"ok": True, "asset": asset}, "application/json"
            if method == "POST" and route == "/api/director/tasks":
                task = self.app.create_task(_json(body))
                return 200, {"ok": task["status"] not in ("failed", "refused", "submit_uncertain", "quote_failed"), "task": _view(task)}, "application/json"
            parts = route.strip("/").split("/")
            if len(parts) >= 4 and parts[:3] == ["api", "director", "tasks"]:
                task_id = parts[3]
                if method == "GET" and len(parts) == 4:
                    task = self.app.store.get(task_id)
                    if task is None:
                        raise DirectorRefusal("not_found", "Task not found", 404)
                    return 200, {"ok": True, "task": _view(task)}, "application/json"
                if method == "GET" and len(parts) == 5 and parts[4] == "result":
                    data, media = self.app.read_result(task_id)
                    return 200, data, media
                if method == "POST" and len(parts) == 5 and parts[4] == "confirm":
                    payload = _json(body or b"{}")
                    task = self.app.confirm(task_id, continue_remaining=bool(payload.get("continue_remaining")))
                    return 200, {"ok": task["status"] not in ("failed", "refused", "submit_uncertain"), "task": _view(task)}, "application/json"
                if method == "POST" and len(parts) == 5 and parts[4] == "poll":
                    task = self.app.poll(task_id)
                    return 200, {"ok": task.get("error", {}).get("code") not in ("poll_budget_exhausted", "provider_failed") if isinstance(task.get("error"), dict) else True, "task": _view(task)}, "application/json"
                if method == "POST" and len(parts) == 5 and parts[4] == "cancel":
                    task = self.app.cancel(task_id)
                    return 200, {"ok": True, "task": _view(task)}, "application/json"
            return 404, {"ok": False, "error": {"code": "not_found", "message": "No such director route"}}, "application/json"
        except DirectorRefusal as exc:
            payload = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
            if getattr(exc, "task", None):
                payload["task"] = _view(exc.task)
            return exc.http_status, payload, "application/json"

    def _static(self, route):
        relative = "index.html" if route in ("", "/") else route.lstrip("/")
        if ".." in relative.split("/"):
            return 404, {"ok": False, "error": {"code": "not_found", "message": "Not found"}}, "application/json"
        path = (self.repo_root / relative).resolve()
        if not str(path).startswith(str(self.repo_root.resolve())) or not path.is_file():
            return 404, b"not found", "text/plain"
        media = "text/html; charset=utf-8"
        if path.suffix == ".js":
            media = "text/javascript; charset=utf-8"
        elif path.suffix == ".json":
            media = "application/json"
        return 200, path.read_bytes(), media


def _json(body):
    if not body:
        return {}
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DirectorRefusal("bad_json", "Request body is not JSON", 400) from exc
    if not isinstance(value, dict):
        raise DirectorRefusal("bad_json", "JSON object required", 400)
    return value


def _view(task):
    hidden = {"http_status"}
    return {key: value for key, value in task.items() if key not in hidden}


def serve(repo_root, data_dir, host="127.0.0.1", port=8765, venice_api_key="", minimax_api_key=""):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    app = build_app(repo_root, data_dir, UrllibTransport(), UrllibTransport(), venice_api_key, minimax_api_key)
    handler_api = DirectorHandler(app, repo_root)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _dispatch(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            status, payload, media = handler_api.handle(method, self.path, raw, self.headers)
            if isinstance(payload, (dict, list)):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                media = "application/json; charset=utf-8"
            else:
                data = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            return

    httpd = ThreadingHTTPServer((host, port), Handler)
    print("director listening on http://%s:%s" % (host, port))
    httpd.serve_forever()
