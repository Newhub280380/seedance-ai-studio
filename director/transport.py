import json
from urllib import error, request

from director.errors import TransportFailure


class UrllibTransport:
    """One attempt. No retry loop."""

    def request(self, method, url, headers, body, timeout):
        req = request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with request.urlopen(req, timeout=timeout) as response:
                payload = response.read()
                headers_out = {key.lower(): value for key, value in response.headers.items()}
                return type("HttpResult", (), {
                    "status": response.status,
                    "headers": headers_out,
                    "body": payload,
                })()
        except error.HTTPError as exc:
            payload = exc.read()
            headers_out = {key.lower(): value for key, value in exc.headers.items()}
            return type("HttpResult", (), {
                "status": exc.code,
                "headers": headers_out,
                "body": payload,
            })()
        except TimeoutError as exc:
            raise TransportFailure(str(exc), uncertain=True) from exc
        except error.URLError as exc:
            reason = exc.reason
            uncertain = isinstance(reason, TimeoutError)
            raise TransportFailure(str(exc), uncertain=uncertain) from exc


def json_body(result):
    if not result.body:
        return None
    try:
        return json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
