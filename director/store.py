import json
import os
import threading
from pathlib import Path


class TaskStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tasks = {}
        self._by_client = {}
        for path in self.root.glob("*.json"):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(task, dict) and task.get("id"):
                self._tasks[task["id"]] = task
                client = task.get("client_request_id")
                if client:
                    self._by_client[client] = task["id"]

    def get(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            return None if task is None else json.loads(json.dumps(task))

    def find_client(self, client_request_id):
        with self._lock:
            task_id = self._by_client.get(client_request_id)
            if not task_id:
                return None
            return json.loads(json.dumps(self._tasks[task_id]))

    def list_tasks(self):
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return [json.loads(json.dumps(item)) for item in tasks]

    def save(self, task):
        with self._lock:
            self._tasks[task["id"]] = task
            client = task.get("client_request_id")
            if client:
                self._by_client[client] = task["id"]
            path = self.root / f"{task['id']}.json"
            payload = json.dumps(task, ensure_ascii=False, indent=2).encode("utf-8")
            temporary = path.with_suffix(".json.tmp")
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        return json.loads(json.dumps(task))
