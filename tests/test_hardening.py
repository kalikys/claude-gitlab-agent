import os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from triggers import Job  # noqa: E402
import runner  # noqa: E402
import gl as glcli  # noqa: E402
from test_runner import home  # noqa: E402

JOB = Job("issue", "acme/kb", 3, "x", {})


def allowed_denied(h):
    cmd = runner.build_command(JOB, h, None, "p")
    return cmd[cmd.index("--allowedTools") + 1].split(","), cmd[cmd.index("--disallowedTools") + 1].split(",")


class EnvTest(unittest.TestCase):
    def test_env_has_no_token(self):
        old = dict(os.environ)
        os.environ.update(GITLAB_TOKEN="tok", MCP_FIRECRAWL_KEY="k", RANDOM_SECRET="s")
        try:
            env = runner.agent_env(Path("/h"))
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn("GITLAB_TOKEN", env)
        self.assertNotIn("RANDOM_SECRET", env)
        self.assertNotIn("MCP_FIRECRAWL_KEY", env)
        self.assertEqual(env["AGENT_HOME"], "/h")


class TokenFileTest(unittest.TestCase):
    def secrets(self):
        p = Path(tempfile.mkdtemp()) / "env"
        p.write_text("GITLAB_TOKEN=filetok\nBOT_USERNAME=claude-bot\n")
        return p

    def test_gl_reads_token_from_file(self):
        p = self.secrets()
        old = os.environ.pop("GITLAB_TOKEN", None)
        os.environ["AGENT_SECRETS"] = str(p)
        try:
            self.assertEqual(glcli.client().token, "filetok")
        finally:
            os.environ.pop("AGENT_SECRETS")
            if old:
                os.environ["GITLAB_TOKEN"] = old

    def test_askpass_reads_token_from_file(self):
        p = self.secrets()
        env = {k: v for k, v in os.environ.items() if k != "GITLAB_TOKEN"}
        env["AGENT_SECRETS"] = str(p)
        r = subprocess.run([str(HERE.parent / "askpass.sh"), "Password for 'https://claude-bot@gitlab.com': "],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.strip(), "filetok")


if __name__ == "__main__":
    unittest.main()


class BodyFileTest(unittest.TestCase):
    def test_body_file_outside_work_refused(self):
        h = Path(tempfile.mkdtemp())
        (h / "work").mkdir()
        (h / "secrets").mkdir()
        (h / "secrets" / "env").write_text("GITLAB_TOKEN=x")
        (h / "work" / "ok.md").write_text("hello")
        os.environ["AGENT_HOME"] = str(h)
        try:
            self.assertEqual(glcli.read_body_file(str(h / "work" / "ok.md")), "hello")
            with self.assertRaises(ValueError):
                glcli.read_body_file(str(h / "secrets" / "env"))
            (h / "work" / "link.md").symlink_to(h / "secrets" / "env")
            with self.assertRaises(ValueError):
                glcli.read_body_file(str(h / "work" / "link.md"))
        finally:
            os.environ.pop("AGENT_HOME")
