import json

from director.errors import TransportFailure
from director.transport import json_body


class VeniceClient:
    def __init__(self, transport, api_key, base="https://api.venice.ai"):
        self.transport = transport
        self.api_key = api_key
        self.base = base.rstrip("/")

    def post(self, path, payload, timeout=30):
        return self._send("POST", path, payload, timeout)

    def _send(self, method, path, payload, timeout):
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        raw = json.dumps(payload).encode("utf-8")
        try:
            result = self.transport.request(method, self.base + path, headers, raw, timeout)
        except TransportFailure:
            raise
        except TimeoutError as exc:
            raise TransportFailure(str(exc), uncertain=True) from exc
        except Exception as exc:
            raise TransportFailure(str(exc), uncertain=False) from exc
        return result, json_body(result)


class MinimaxClient:
    def __init__(self, transport, api_key, base="https://api.minimax.io"):
        self.transport = transport
        self.api_key = api_key
        self.base = base.rstrip("/")

    def post(self, path, payload, timeout=30):
        return self._send("POST", path, payload, timeout)

    def get(self, path, timeout=30):
        return self._send("GET", path, None, timeout)

    def get_absolute(self, url, timeout=30):
        try:
            result = self.transport.request("GET", url, {}, None, timeout)
        except TransportFailure:
            raise
        except TimeoutError as exc:
            raise TransportFailure(str(exc), uncertain=False) from exc
        except Exception as exc:
            raise TransportFailure(str(exc), uncertain=False) from exc
        return result

    def _send(self, method, path, payload, timeout):
        headers = {"Authorization": "Bearer " + self.api_key}
        raw = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            raw = json.dumps(payload).encode("utf-8")
        try:
            result = self.transport.request(method, self.base + path, headers, raw, timeout)
        except TransportFailure:
            raise
        except TimeoutError as exc:
            raise TransportFailure(str(exc), uncertain=method == "POST") from exc
        except Exception as exc:
            raise TransportFailure(str(exc), uncertain=False) from exc
        return result, json_body(result)
