#!/usr/bin/env python3
"""Manual check on the worker host: does the sandboxed agent start, and is the token out of its reach?

Run as the claude user: python3 ~/gitlab-agent/app/tests/sandbox_probe.py
Expected after setup: SANDBOX_ECHO_OK printed, reading the secrets file denied, network denied.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runner  # noqa: E402
from triggers import Job  # noqa: E402

home = Path(os.environ.get("AGENT_HOME", Path.home() / "gitlab-agent"))
job = Job("issue", "acme/probe", 0, "sandbox probe")
prompt = ("Это проверка песочницы. Выполни по очереди три команды в Bash и перепиши их вывод дословно: "
          f"1) echo SANDBOX_ECHO_OK 2) wc -c {home}/secrets/env 3) python3 -c \"import urllib.request;"
          "print(urllib.request.urlopen('https://example.com',timeout=5).status)\". Больше ничего не делай.")
if len(sys.argv) > 1 and sys.argv[1] == "web":
    prompt = ("Проверка доступа в интернет. 1) Инструментом WebFetch открой https://example.com и напиши заголовок страницы. "
              "2) Инструментом WebSearch найди 'GitLab latest release' и напиши первый результат одной строкой. "
              "Больше ничего не делай.")
cmd = runner.build_command(job, home, "haiku", prompt, os.environ.get("CLAUDE_BIN", str(Path.home() / ".local/bin/claude")))
runner.fresh_clones(home)
cwd = home / "work" / "jobs" / "probe"
cwd.mkdir(parents=True, exist_ok=True)
p = subprocess.run(cmd, cwd=cwd, env=runner.agent_env(home), capture_output=True, text=True, timeout=300)
print("exit:", p.returncode)
print(p.stdout[-3000:])
print(p.stderr[-1500:])
