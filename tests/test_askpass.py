import os, subprocess, unittest
from pathlib import Path

ASKPASS = str(Path(__file__).resolve().parent.parent / "askpass.sh")


def ask(prompt):
    env = dict(os.environ, GITLAB_TOKEN="tok123")
    r = subprocess.run([ASKPASS, prompt], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip()


class AskpassTest(unittest.TestCase):
    def test_gitlab_username(self):
        self.assertEqual(ask("Username for 'https://gitlab.com': "), (0, "claude-bot"))

    def test_gitlab_password(self):
        self.assertEqual(ask("Password for 'https://claude-bot@gitlab.com': "), (0, "tok123"))

    def test_other_host_gets_nothing(self):
        code, out = ask("Password for 'https://claude-bot@evil.example': ")
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_lookalike_host_gets_nothing(self):
        code, out = ask("Password for 'https://claude-bot@gitlab.com.evil.example': ")
        self.assertEqual(out, "")
        self.assertNotEqual(code, 0)
