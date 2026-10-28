"""Run one Claude session for a job: fresh copies of the knowledge base, a sandboxed agent, a log line.

Security model: every shell command of the agent runs in Claude Code's OS sandbox (bubblewrap): no network,
no reading of the token or Claude credentials, no writing outside the work folders. Only gl.py runs outside
the sandbox; it is the worker's own read-only code and checks its arguments. The agent never gets the token.
"""
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import config
import gl as glcli
from triggers import Job

APP_DIR = Path(__file__).resolve().parent
BOT_NAME = config.bot_name()
BOT_EMAIL = config.bot_email()
REPOS = {"kb": config.kb_project(), "team": config.team_project()}
PASS_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "TERM", "XDG_RUNTIME_DIR",
            "GITLAB_GROUP", "BOT_USERNAME", "KB_PROJECT", "TEAM_PROJECT", "WORKER_PROJECT", "CLAUDE_MODEL", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


@dataclass
class RunResult:
    ok: bool
    cost_usd: float | None
    session_id: str | None
    summary: str


def mcp_servers(home: Path) -> list[str]:
    try:
        return sorted(json.loads((home / "app/agent/mcp.json").read_text()).get("mcpServers", {}))
    except (OSError, ValueError):
        return []


ROUTINE_KINDS = ("inbox", "stale", "digest")
UNKNOWN_COST_USD = 2.0   # a run whose cost is unknown (timeout, crash) counts as this much against the daily limit


def run_cost(res: "RunResult") -> float:
    return res.cost_usd if res.cost_usd is not None else UNKNOWN_COST_USD
REGISTRIES = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "registry.yarnpkg.com",
              "proxy.golang.org", "sum.golang.org", "index.crates.io", "static.crates.io", "crates.io",
              "rubygems.org", "repo.maven.apache.org", "repo1.maven.org"]


def model_for(job: Job, env: dict) -> str | None:
    """Routine knowledge-base chores run on a cheaper model; work with people uses the default (or CLAUDE_MODEL)."""
    if job.kind in ROUTINE_KINDS:
        return env.get("CLAUDE_MODEL_ROUTINE", "sonnet")
    return env.get("CLAUDE_MODEL") or None


def settings(home: Path) -> dict:
    protected_read = [str(home / "secrets"), str(home / "state"), str(home / "cache"), "~/.claude"]
    protected_write = [str(home / "app"), str(home / "src"), str(home / "state"), str(home / "cache"),
                       str(home / "logs"), str(home / "secrets")]
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "excludedCommands": [f"python3 {home}/app/gl.py *", "python3 ~/gitlab-agent/app/gl.py *"],
            "filesystem": {"denyRead": protected_read, "denyWrite": protected_write},
            # Package registries only, so the agent can install dependencies and run tests. Nothing else.
            "network": {"allowedDomains": REGISTRIES, "strictAllowlist": True},
        },
        # Read/Edit/Write tools are outside the sandbox and follow permission rules ("//" = absolute path).
        "permissions": {"deny": [f"Read(/{p}/**)" for p in protected_read if p.startswith("/")]
                        + ["Read(~/.claude/**)", "Read(//proc/**)"]
                        + [f"{t}(/{p}/**)" for p in protected_write for t in ("Edit", "Write")]},
    }


def runtime_mcp_config(home: Path) -> Path:
    """mcp.json with ${MCP_*} filled from the secrets file, written where the agent cannot read (cache/).
    MCP secrets reach the MCP servers only, never the agent's own environment."""
    import re
    from agent_secrets import read_env_file
    text = (home / "app/agent/mcp.json").read_text(encoding="utf-8") if (home / "app/agent/mcp.json").exists() \
        else '{"mcpServers": {}}'
    values = {**{k: v for k, v in os.environ.items() if k.startswith("MCP_")}, **read_env_file(home / "secrets/env")}
    text = re.sub(r"\$\{(MCP_[A-Z0-9_]+)\}", lambda m: json.dumps(values.get(m.group(1), ""))[1:-1], text)
    path = home / "cache" / "mcp-runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def build_command(job: Job, home: Path, model: str | None, prompt: str, claude: str = "claude") -> list[str]:
    kb, team = home / "work/kb", home / "work/team"
    allowed = ["Read", "Grep", "Glob", "Edit", "Write", "WebSearch", "WebFetch", "Skill", "Bash"]
    allowed += [f"mcp__{name}" for name in mcp_servers(home)]
    cmd = [claude, "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk",
           "--settings", json.dumps(settings(home)),
           "--mcp-config", str(runtime_mcp_config(home)), "--strict-mcp-config",
           "--append-system-prompt-file", str(kb / "CLAUDE.md"),
           "--add-dir", str(kb), "--add-dir", str(team),
           "--allowedTools", ",".join(allowed)]
    if model:
        cmd += ["--model", model]
    return cmd


def render_prompt(job: Job, context: str) -> str:
    text = (APP_DIR / "prompts" / f"{job.kind}.md").read_text(encoding="utf-8")
    values = {"project": job.project, "iid": str(job.iid or ""), "reason": job.reason, "context": context,
              "approver": job.extra.get("approver", ""), "approved_at": job.extra.get("approved_at", ""),
              "bot": BOT_NAME}
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


def job_dir(home: Path, job: Job) -> Path:
    name = f"{job.project.replace('/', '-')}-{job.kind}" + (f"-{job.iid}" if job.iid else "")
    d = home / "work" / "jobs" / name
    (d / ".claude").mkdir(parents=True, exist_ok=True)
    link = d / ".claude" / "skills"
    if not link.is_symlink():
        link.symlink_to(home / "app/agent/skills")
    return d


def agent_env(home: Path) -> dict:
    """Minimal environment for Claude: no GitLab token, no askpass, no MCP secrets, bot identity for commits."""
    env = {k: v for k, v in os.environ.items() if k in PASS_ENV}
    env.update(AGENT_HOME=str(home), GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_NAME=BOT_NAME, GIT_AUTHOR_EMAIL=BOT_EMAIL,
               GIT_COMMITTER_NAME=BOT_NAME, GIT_COMMITTER_EMAIL=BOT_EMAIL)
    return env


def git_env() -> dict:
    """Environment for the worker's own git calls (clean config, token via askpass)."""
    return glcli.safe_git_env()


def fresh_clones(home: Path) -> None:
    """Re-clone the knowledge repos before every run: the agent may have changed anything inside work/,
    including .git/config, and the worker must not run git on that."""
    os.environ.setdefault("AGENT_HOME", str(home))
    for d, project in REPOS.items():
        target = home / "work" / d
        if target.exists():
            shutil.rmtree(target)
        glcli.clone_repo(project, str(target))


def log(home: Path, record: dict) -> None:
    d = home / "logs"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{date.today().isoformat()}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(job: Job, home: Path, context: str, timeout: int = 1800, claude: str | None = None,
        model: str | None = None, pull: bool = True) -> RunResult:
    if pull:
        fresh_clones(home)
    cwd = job_dir(home, job)
    claude = claude or os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
    cmd = build_command(job, home, model or model_for(job, os.environ), render_prompt(job, context), claude)
    started = datetime.now(timezone.utc).isoformat()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=agent_env(home), capture_output=True, text=True, timeout=timeout)
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except ValueError:
            out = {}
        ok = p.returncode == 0 and not out.get("is_error", True)
        res = RunResult(ok, out.get("total_cost_usd"), out.get("session_id"),
                        str(out.get("result") or p.stderr[-500:])[:2000])
    except subprocess.TimeoutExpired:
        res = RunResult(False, None, None, f"timeout after {timeout}s")
    log(home, {"started": started, "job": job.key, "reason": job.reason, "ok": res.ok, "cost_usd": res.cost_usd,
               "session_id": res.session_id, "summary": res.summary})
    return res
