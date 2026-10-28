"""In-memory stand-in for gitlab.GitLab used by poll/schedule tests."""


class FakeGL:
    def __init__(self, routes=None, members=None, pids=None):
        self.routes = routes or {}          # path -> data, or callable(params) -> data
        self._members = members or {"alice": "K", "claude-bot": "Bot"}
        self.pids = pids or {"acme/kb": 7, "acme/kb": 7}
        self.posts, self.puts = [], []
        self.group = "acme"

    def members(self):
        return self._members

    def project_id(self, path):
        return self.pids[path]

    def get(self, path, **params):
        val = self.routes.get(path, [])
        return val(params) if callable(val) else val

    def post(self, path, data):
        self.posts.append((path, data))
        return {"id": 999, "iid": 99, "web_url": "u"}

    def put(self, path, data):
        self.puts.append((path, data))
        return {}
