"""A fake urllib opener for GitLab API tests."""
import io
import json
import urllib.parse


class Resp(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data if isinstance(data, bytes) else json.dumps(data).encode())
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHTTP:
    """routes: {(METHOD, "path?query-or-not"): data | (data, headers)}. Matching ignores per_page."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=None):
        u = urllib.parse.urlsplit(req.full_url)
        path = u.path.split("/api/v4/", 1)[1]
        q = dict(urllib.parse.parse_qsl(u.query))
        q.pop("per_page", None)
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((req.get_method(), path, q, body))
        key_q = path + ("?" + urllib.parse.urlencode(sorted(q.items())) if q else "")
        for key in ((req.get_method(), key_q), (req.get_method(), path)):
            if key in self.routes:
                val = self.routes[key]
                data, headers = val if isinstance(val, tuple) else (val, {})
                return Resp(data, headers)
        raise AssertionError(f"unexpected request {req.get_method()} {key_q}")
