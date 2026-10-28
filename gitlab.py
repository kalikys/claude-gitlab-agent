"""Small GitLab REST client (stdlib). Every project must belong to the configured group."""
import json
import urllib.parse
import urllib.request
from typing import Any

UA = "claude-bot/1.0"


class GitLab:
    def __init__(self, token: str, group: str, base: str = "https://gitlab.com/api/v4", opener=None):
        self.token, self.group, self.base = token, group, base.rstrip("/")
        self.opener = opener or urllib.request.urlopen
        self._pids: dict[str, int] = {}
        self._members: dict[str, str] | None = None

    def _call(self, method: str, path: str, params: dict | None = None, data: dict | None = None):
        url = f"{self.base}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers={
            "PRIVATE-TOKEN": self.token, "User-Agent": UA, "Content-Type": "application/json"})
        with self.opener(req, timeout=30) as r:
            raw = r.read()
            return (json.loads(raw) if raw else None), r.headers

    def get(self, path: str, **params) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        params.setdefault("per_page", 100)
        data, headers = self._call("GET", path, params)
        if not isinstance(data, list):
            return data
        out = list(data)
        nxt = headers.get("X-Next-Page") if headers else None
        while nxt:
            data, headers = self._call("GET", path, dict(params, page=nxt))
            out += data
            nxt = headers.get("X-Next-Page") if headers else None
        return out

    def get_bytes(self, path: str, limit: int = 50 * 1024 * 1024) -> bytes:
        req = urllib.request.Request(f"{self.base}/{path}", headers={"PRIVATE-TOKEN": self.token, "User-Agent": UA})
        with self.opener(req, timeout=120) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"file is larger than {limit // (1024 * 1024)} MB")
        return data

    def post(self, path: str, data: dict) -> Any:
        return self._call("POST", path, data=data)[0]

    def put(self, path: str, data: dict) -> Any:
        return self._call("PUT", path, data=data)[0]

    def delete(self, path: str) -> Any:
        return self._call("DELETE", path)[0]

    def in_group(self, path: str) -> bool:
        return path.startswith(self.group + "/")

    def project_id(self, path_with_namespace: str) -> int:
        if not self.in_group(path_with_namespace):
            raise ValueError(f"project {path_with_namespace} is outside group {self.group}")
        if path_with_namespace not in self._pids:
            proj = self.get("projects/" + urllib.parse.quote(path_with_namespace, safe=""))
            resolved = str(proj.get("path_with_namespace", path_with_namespace)).lower()
            if not resolved.startswith(self.group.lower() + "/"):
                raise ValueError(f"project {path_with_namespace} resolves to {resolved}, outside group {self.group}")
            self._pids[path_with_namespace] = proj["id"]
        return self._pids[path_with_namespace]

    def members(self) -> dict[str, str]:
        if self._members is None:
            self._members = {m["username"]: m["name"] for m in self.get(f"groups/{self.group}/members/all")}
        return self._members
