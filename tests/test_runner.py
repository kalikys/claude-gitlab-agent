import json, os, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from triggers import Job  # noqa: E402
import runner  # noqa: E402

FAKE = str(HERE / "fake_claude.sh")


def home():
    h = Path(tempfile.mkdtemp(prefix="agent-home-"))
    (h / "app" / "agent" / "skills").mkdir(parents=True)
    (h / "app" / "agent" / "mcp.json").write_text('{"mcpServers": {"firecrawl": {"command": "x"}}}')
    (h / "work" / "kb").mkdir(parents=True)
    (h / "work" / "kb" / "CLAUDE.md").write_text("rules")
    (h / "work" / "team").mkdir(parents=True)
    return h


JOB = Job("issue", "acme/kb", 3, "новая задача", {})


class CommandTest(unittest.TestCase):
    def test_command_allows_web_and_mcp_config(self):
        h = home()
        cmd = runner.build_command(JOB, h, None, "prompt")
        allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
        for t in ("WebSearch", "WebFetch", "Skill", "Bash", "mcp__firecrawl"):
            self.assertIn(t, allowed)
        self.assertEqual(cmd[cmd.index("--mcp-config") + 1], str(h / "cache/mcp-runtime.json"))
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")

    def test_command_kb_claude_md_appended(self):
        h = home()
        cmd = runner.build_command(JOB, h, None, "prompt")
        self.assertEqual(cmd[cmd.index("--append-system-prompt-file") + 1], str(h / "work/kb/CLAUDE.md"))
        self.assertIn(str(h / "work/kb"), cmd)
        self.assertIn(str(h / "work/team"), cmd)

    def test_model_flag(self):
        cmd = runner.build_command(JOB, home(), "opus", "p")
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")

    def test_render_prompt_substitutes(self):
        job = Job("approved", "acme/kb", 3, "одобрено", {"approver": "alice"})
        out = runner.render_prompt(job, "CONTEXT-HERE")
        self.assertIn("acme/kb", out)
        self.assertIn("CONTEXT-HERE", out)
        self.assertIn("@alice", out)
        self.assertNotIn("{", out.replace("{\"waiting\"", ""))


class RunTest(unittest.TestCase):
    def run_job(self, mode, timeout=30):
        # The agent gets a stripped environment, so the fake's settings travel in a wrapper script.
        h = home()
        cwd_file = h / "cwd.txt"
        wrapper = h / "claude"
        wrapper.write_text(f'#!/bin/sh\nFAKE_MODE={mode} FAKE_CWD_FILE={cwd_file} exec {FAKE} "$@"\n')
        wrapper.chmod(0o755)
        res = runner.run(JOB, h, context="ctx", timeout=timeout, claude=str(wrapper), pull=False)
        return h, res, cwd_file

    def test_run_parses_cost_and_session(self):
        h, res, _ = self.run_job("ok")
        self.assertTrue(res.ok)
        self.assertEqual((res.cost_usd, res.session_id), (0.12, "s1"))
        logs = list((h / "logs").glob("*.jsonl"))
        self.assertEqual(json.loads(logs[0].read_text().splitlines()[0])["session_id"], "s1")

    def test_run_timeout_not_ok(self):
        _, res, _ = self.run_job("sleep", timeout=1)
        self.assertFalse(res.ok)
        self.assertIn("timeout", res.summary)

    def test_run_error_not_ok(self):
        _, res, _ = self.run_job("error")
        self.assertFalse(res.ok)

    def test_job_dir_has_skills_symlink(self):
        h, _, cwd_file = self.run_job("ok")
        jobdir = Path(cwd_file.read_text().strip())
        self.assertEqual(jobdir.name, "acme-kb-issue-3")
        link = jobdir / ".claude" / "skills"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (h / "app/agent/skills").resolve())


if __name__ == "__main__":
    unittest.main()
