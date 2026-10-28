import json, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from gitlab import GitLab  # noqa: E402
from fakehttp import FakeHTTP  # noqa: E402
import gl as glcli  # noqa: E402
import runner  # noqa: E402
from triggers import Job  # noqa: E402
from test_runner import home  # noqa: E402

P = "projects/acme%2Fkb"
BASE = {("GET", P): {"id": 7, "path_with_namespace": "acme/kb"}}


def client(routes):
    http = FakeHTTP({**BASE, **routes})
    return GitLab("t", "acme", opener=http), http


class RegistryTest(unittest.TestCase):
    def test_package_registries_open_rest_closed(self):
        cmd = runner.build_command(Job("issue", "p", 1, "r"), home(), None, "p")
        domains = json.loads(cmd[cmd.index("--settings") + 1])["sandbox"]["network"]["allowedDomains"]
        for d in ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "proxy.golang.org"):
            self.assertIn(d, domains)
        self.assertNotIn("gitlab.com", domains)
        self.assertNotIn("*", domains)


class PipelineTest(unittest.TestCase):
    def test_pipeline_text(self):
        g, _ = client({("GET", "projects/7/pipelines?ref=claude%2F3-x"): [{"id": 50, "status": "failed", "web_url": "w",
                                                                          "sha": "abcdef123456"}],
                       ("GET", "projects/7/pipelines/50/jobs"): [{"id": 501, "name": "test", "stage": "test",
                                                                  "status": "failed"},
                                                                 {"id": 502, "name": "lint", "stage": "test",
                                                                  "status": "success"}]})
        out = glcli.pipeline_text(g, "acme/kb", "claude/3-x")
        self.assertIn("failed", out)
        self.assertIn("501", out)
        self.assertIn("job-log", out)

    def test_job_log_tail_and_ansi_stripped(self):
        log = ("\n".join(f"line {i}" for i in range(500)) + "\n\x1b[31mERROR boom\x1b[0m\n").encode()
        g, _ = client({("GET", "projects/7/jobs/501/trace"): log})
        out = glcli.job_log(g, "acme/kb", 501, tail=5)
        self.assertEqual(out.splitlines()[-1], "ERROR boom")
        self.assertEqual(len(out.splitlines()), 5)


class MrDiffTest(unittest.TestCase):
    def test_mr_diff_text(self):
        g, _ = client({("GET", "projects/7/merge_requests/4/diffs"): [
            {"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1 +1 @@\n-x = 1\n+x = 2\n"},
            {"old_path": "b.md", "new_path": "b.md", "diff": "@@ -0,0 +1 @@\n+hi\n"}]})
        out = glcli.mr_diff(g, "acme/kb", 4)
        self.assertIn("+x = 2", out)
        self.assertIn("=== a.py", out)
        only = glcli.mr_diff(g, "acme/kb", 4, file="b.md")
        self.assertNotIn("a.py", only)

    def test_line_comment_uses_diff_refs(self):
        g, http = client({("GET", "projects/7/merge_requests/4"): {"diff_refs": {"base_sha": "b", "head_sha": "h",
                                                                                 "start_sha": "s"}},
                          ("POST", "projects/7/merge_requests/4/discussions"): {"id": "d1"}})
        glcli.mr_line_comment(g, "acme/kb", 4, "a.py", 12, "Тут ошибка")
        body = http.calls[-1][3]
        self.assertEqual(body["body"], "Тут ошибка")
        pos = body["position"]
        self.assertEqual((pos["base_sha"], pos["head_sha"], pos["start_sha"]), ("b", "h", "s"))
        self.assertEqual((pos["new_path"], pos["new_line"], pos["position_type"]), ("a.py", 12, "text"))


if __name__ == "__main__":
    unittest.main()


class RedactTest(unittest.TestCase):
    def test_job_log_redacts_secrets(self):
        log = (b"token glpat-" b"abcdefghijklmnopqrstuv\nAuthorization: Bearer abc.def.ghi\n"
               b"git clone https://user:s3cretpass@gitlab.com/x.git\nok\n")
        g, _ = client({("GET", "projects/7/jobs/9/trace"): log})
        out = glcli.job_log(g, "acme/kb", 9)
        for leaked in ("glpat-" "abcdefghijklmnopqrstuv", "abc.def.ghi", "s3cretpass"):
            self.assertNotIn(leaked, out)
        self.assertIn("ok", out)


class McpSecretsTest(unittest.TestCase):
    def test_mcp_secrets_not_in_agent_env_but_in_runtime_config(self):
        import os, tempfile
        h = home()
        (h / "secrets").mkdir()
        (h / "secrets" / "env").write_text("GITLAB_TOKEN=t\nMCP_FIRECRAWL_KEY=fk123\n")
        (h / "app" / "agent" / "mcp.json").write_text(
            '{"mcpServers": {"firecrawl": {"command": "npx", "env": {"API_KEY": "${MCP_FIRECRAWL_KEY}"}}}}')
        os.environ["MCP_FIRECRAWL_KEY"] = "fk123"
        try:
            env = runner.agent_env(h)
            path = runner.runtime_mcp_config(h)
        finally:
            os.environ.pop("MCP_FIRECRAWL_KEY")
        self.assertNotIn("MCP_FIRECRAWL_KEY", env)
        self.assertIn("fk123", path.read_text())
        self.assertTrue(str(path).startswith(str(h / "cache")))
        cmd = runner.build_command(Job("issue", "p", 1, "r"), h, None, "p")
        self.assertEqual(cmd[cmd.index("--mcp-config") + 1], str(path))
