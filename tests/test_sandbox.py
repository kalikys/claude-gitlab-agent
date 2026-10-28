import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from triggers import Job  # noqa: E402
import runner  # noqa: E402
import gl as glcli  # noqa: E402
from test_runner import home  # noqa: E402

JOB = Job("issue", "acme/kb", 3, "x", {})


def settings(h):
    cmd = runner.build_command(JOB, h, None, "p")
    return json.loads(cmd[cmd.index("--settings") + 1]), cmd


class SandboxSettingsTest(unittest.TestCase):
    def test_sandbox_strict(self):
        s, _ = settings(home())
        sb = s["sandbox"]
        self.assertTrue(sb["enabled"])
        self.assertTrue(sb["failIfUnavailable"])
        self.assertFalse(sb["allowUnsandboxedCommands"])
        self.assertNotIn("gitlab.com", sb["network"]["allowedDomains"])
        self.assertTrue(sb["network"]["strictAllowlist"])

    def test_only_gl_runs_outside_sandbox(self):
        h = home()
        s, _ = settings(h)
        self.assertEqual(sorted(s["sandbox"]["excludedCommands"]),
                         sorted([f"python3 {h}/app/gl.py *", "python3 ~/gitlab-agent/app/gl.py *"]))

    def test_secrets_and_app_protected(self):
        h = home()
        s, _ = settings(h)
        self.assertIn(str(h / "secrets"), s["sandbox"]["filesystem"]["denyRead"])
        self.assertIn("~/.claude", s["sandbox"]["filesystem"]["denyRead"])
        self.assertIn(str(h / "app"), s["sandbox"]["filesystem"]["denyWrite"])
        deny = s["permissions"]["deny"]
        self.assertIn(f"Read(/{h}/secrets/**)", deny)
        self.assertIn(f"Edit(/{h}/app/**)", deny)

    def test_bash_allowed_because_sandboxed(self):
        _, cmd = settings(home())
        allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
        self.assertIn("Bash", allowed)
        self.assertIn("WebFetch", allowed)


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class CloneOnlyTest(unittest.TestCase):
    def setUp(self):
        self.h = Path(tempfile.mkdtemp(prefix="glhome-"))
        (self.h / "work").mkdir()
        self.remote_base = Path(tempfile.mkdtemp(prefix="remote-"))
        bare = self.remote_base / "acme" / "repo.git"
        bare.parent.mkdir(parents=True)
        git("init", "-q", "--bare", "-b", "main", str(bare))
        seed = Path(tempfile.mkdtemp())
        git("init", "-q", "-b", "main", cwd=seed)
        (seed / "a.md").write_text("a")
        git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=seed)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=seed)
        git("push", "-q", str(bare), "main", cwd=seed)
        self.bare = bare
        os.environ["AGENT_HOME"] = str(self.h)

    def tearDown(self):
        os.environ.pop("AGENT_HOME")

    def test_clone_into_work(self):
        dest = glcli.clone_repo("acme/repo", str(self.h / "work" / "kb"), remote_base=f"file://{self.remote_base}")
        self.assertTrue((Path(dest) / "a.md").exists())

    def test_clone_outside_work_refused(self):
        with self.assertRaises(ValueError):
            glcli.clone_repo("acme/repo", str(self.h / "elsewhere"), remote_base=f"file://{self.remote_base}")

    def test_clone_outside_group_refused(self):
        with self.assertRaises(ValueError):
            glcli.clone_repo("evil/kb", str(self.h / "work" / "x"), remote_base=f"file://{self.remote_base}")

    def test_push_via_clean_cache(self):
        dest = Path(glcli.clone_repo("acme/repo", str(self.h / "work" / "kb"),
                                     remote_base=f"file://{self.remote_base}"))
        (dest / "b.md").write_text("b")
        git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=dest)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b", cwd=dest)
        # A hostile hook in the agent's repo must not run during push.
        hook = dest / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\ntouch " + str(self.h / "PWNED") + "\n")
        hook.chmod(0o755)
        glcli.push_repo(str(dest), "acme/repo", "claude/3-x", remote_base=f"file://{self.remote_base}")
        self.assertEqual(git("rev-parse", "claude/3-x", cwd=self.bare), git("rev-parse", "HEAD", cwd=dest))
        self.assertFalse((self.h / "PWNED").exists())
        self.assertEqual(list((self.h / "cache").glob("*")) if (self.h / "cache").exists() else [], [])

    def test_push_bad_branch_refused(self):
        dest = glcli.clone_repo("acme/repo", str(self.h / "work" / "kb"), remote_base=f"file://{self.remote_base}")
        for bad in ("-f", "a..b", "HEAD", "x y", "--force"):
            with self.assertRaises(ValueError, msg=bad):
                glcli.push_repo(dest, "acme/repo", bad, remote_base=f"file://{self.remote_base}")

    def test_push_src_outside_work_refused(self):
        with self.assertRaises(ValueError):
            glcli.push_repo(str(self.h), "acme/repo", "main", remote_base=f"file://{self.remote_base}")


class AgentEnvTest(unittest.TestCase):
    def test_agent_has_no_askpass(self):
        env = runner.agent_env(Path("/h"))
        self.assertNotIn("GIT_ASKPASS", env)
        self.assertNotIn("GITLAB_TOKEN", env)


if __name__ == "__main__":
    unittest.main()


class PushTargetTest(CloneOnlyTest):
    def test_main_only_for_knowledge_base(self):
        dest = glcli.clone_repo("acme/repo", str(self.h / "work" / "kb"), remote_base=f"file://{self.remote_base}")
        for branch in ("main", "master", "feature/x"):
            with self.assertRaises(ValueError, msg=branch):
                glcli.push_repo(dest, "acme/repo", branch, remote_base=f"file://{self.remote_base}")
        self.assertFalse(glcli.push_allowed("acme/gitlab-agent", "main"))
        self.assertTrue(glcli.push_allowed("acme/gitlab-agent", "claude/2-mcp"))
        self.assertTrue(glcli.push_allowed("acme/kb", "main"))
        self.assertTrue(glcli.push_allowed("acme/team", "claude/4-matrix"))
        self.assertFalse(glcli.push_allowed("acme/team", "main"))
