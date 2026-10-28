#!/usr/bin/env python3
"""One polling tick (systemd timer, every 2 minutes): find work in GitLab and run Claude for it.

GITLAB_AGENT_DRY=1 prints the jobs and runs nothing.
"""
import fcntl
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
import gl as glcli
import recurring
import runner
from agent_secrets import token
from gitlab import GitLab
from state import State
from triggers import Job, approved_jobs, dedupe, issue_jobs, mention_jobs, mr_jobs, project_path, seen_key

MAX_PER_TICK = 3
DAILY_MAX = 40
MAX_FAILURES = 3
KB_PROJECT = config.kb_project()
AGENT_WORKER = config.worker_project()


def default_home() -> Path:
    return Path(os.environ.get("AGENT_HOME", Path.home() / "gitlab-agent"))


def notes_of(gl, project_id: int, kind: str, iid: int) -> list[dict]:
    path = "merge_requests" if kind == "mr" else "issues"
    return gl.get(f"projects/{project_id}/{path}/{iid}/notes", order_by="created_at", sort="asc")


def collect(gl, state: State, bot: str, group: str) -> list[Job]:
    members = gl.members()
    by_id = {}
    for params in ({"assignee_username": bot}, {"labels": "claude"}):
        for it in gl.get(f"groups/{group}/issues", state="opened", **params):
            by_id[it["id"]] = it
    issues = list(by_id.values())
    notes = {(i["project_id"], i["iid"]): notes_of(gl, i["project_id"], "issue", i["iid"])
             for i in issues if seen_key(i["project_id"], "issue", i["iid"]) in state.seen}
    mrs = gl.get(f"groups/{group}/merge_requests", state="opened", author_username=bot)
    mr_notes = {(m["project_id"], m["iid"]): notes_of(gl, m["project_id"], "mr", m["iid"]) for m in mrs}
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = {}
    for it in gl.get(f"groups/{group}/issues", labels="approved", state="all", updated_after=since):
        events[(project_path(it["web_url"]), it["iid"])] = gl.get(
            f"projects/{it['project_id']}/issues/{it['iid']}/resource_label_events")
    # member jobs first: on merge with another job for the same issue, their reason (who joined) is kept
    jobs = member_jobs(gl, state, bot, group)
    jobs += issue_jobs(issues, notes, members, bot, state) + mr_jobs(mrs, mr_notes, members, bot, state)
    jobs += mention_jobs(gl.get("todos", state="pending"), members, bot, group)
    jobs += approved_jobs(events, members, bot, state)
    return dedupe(jobs)


def member_jobs(gl, state: State, bot: str, group: str) -> list[Job]:
    """Someone joined the group: wake the issues labelled claude-members (e.g. a survey that must include newcomers)."""
    current = sorted(u for u in gl.members() if u != bot)
    if not state.known_members:
        state.known_members = current
        return []
    joined = [u for u in current if u not in state.known_members]
    state.known_members = current
    if not joined:
        return []
    who = ", ".join("@" + u for u in joined)
    jobs = []
    for it in gl.get(f"groups/{group}/issues", state="opened", labels="claude-members"):
        jobs.append(Job("issue", project_path(it["web_url"]), it["iid"], f"в группу вступил(и): {who}",
                        {"seen_key": seen_key(it["project_id"], "issue", it["iid"]), "project_id": it["project_id"]}))
    return jobs


def check_lint(gl, state: State, bot: str, kb_project: str = KB_PROJECT) -> None:
    pid = gl.project_id(kb_project)
    pipes = gl.get(f"projects/{pid}/pipelines", ref="main", per_page=1)
    if not pipes or pipes[0].get("status") != "failed" or pipes[0]["sha"] in state.lint_done:
        return
    body = (f"Упала проверка базы знаний на `main` (коммит {pipes[0]['sha'][:8]}): {pipes[0].get('web_url', '')}\n\n"
            "Найди причину по логу пайплайна и исправь коммитом в `main`.")
    gl.post(f"projects/{pid}/issues", {"title": f"Упал lint базы знаний ({pipes[0]['sha'][:8]})", "description": body,
                                       "labels": "kb-lint,claude"})
    state.lint_done.append(pipes[0]["sha"])


def context_for(gl, job: Job) -> str:
    if job.kind in ("issue", "mr", "approved"):
        return glcli.issue_text(gl, job.project, job.iid, mr=job.kind == "mr")
    return ""


def bot_wrote_since(gl, job: Job, bot: str, started: str) -> bool:
    pid = gl.project_id(job.project)
    for n in notes_of(gl, pid, job.kind, job.iid):
        stamp = (n.get("updated_at") or n.get("created_at") or "")[:19]
        if n["author"]["username"] == bot and stamp >= started:
            return True
    return False


def mark_handled(gl, state: State, job: Job) -> None:
    if job.kind in ("issue", "mr"):
        pid = gl.project_id(job.project)
        key = job.extra.get("seen_key") or seen_key(pid, job.kind, job.iid)
        ids = [n["id"] for n in notes_of(gl, pid, job.kind, job.iid)]
        state.seen[key] = max(ids + [state.seen.get(key, 0)])
    if job.kind == "approved":
        state.approved_done.append(job.extra["event_id"])
    for tid in job.extra.get("todo_ids", []):
        try:
            gl.post(f"todos/{tid}/mark_as_done", {})
        except Exception:
            pass


def tick(gl, state: State, home: Path, run_fn, today: date, bot: str, group: str, dry: bool = False,
         save=lambda: None, budget: float | None = None) -> list[Job]:
    jobs = [j for j in collect(gl, state, bot, group) if state.failures.get(j.key, 0) < MAX_FAILURES]
    if dry:
        for j in jobs:
            print(f"would run {j.kind} {j.project}#{j.iid}: {j.reason}")
        return jobs
    day = today.isoformat()
    budget_runs = min(MAX_PER_TICK, DAILY_MAX - state.runs.get(day, 0))
    limit = budget if budget is not None else float(os.environ.get("DAILY_BUDGET_USD", "15"))
    for job in jobs[:max(budget_runs, 0)]:
        if state.cost.get(day, 0.0) >= limit:
            print(f"daily budget ${limit} reached, waiting for tomorrow")
            break
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        res = run_fn(job, home, context_for(gl, job))
        state.runs[day] = state.runs.get(day, 0) + 1
        state.cost[day] = round(state.cost.get(day, 0.0) + runner.run_cost(res), 4)
        if res.ok and job.kind in ("issue", "mr") and not bot_wrote_since(gl, job, bot, started):
            # Claude finished, but nothing reached the issue (e.g. gl.py could not run): retry later.
            res = runner.RunResult(False, res.cost_usd, res.session_id, "no comment from the bot after the run")
        if res.ok:
            state.failures.pop(job.key, None)
            mark_handled(gl, state, job)
        else:
            state.failures[job.key] = state.failures.get(job.key, 0) + 1
            if state.failures[job.key] >= MAX_FAILURES and job.iid:
                glcli.comment(gl, job.project, job.iid,
                              f"Не смог выполнить запуск {MAX_FAILURES} раза подряд (сессия {res.session_id or '?'}, детали в логах воркера). "
                              "Здесь нужен человек: посмотрите задачу или логи воркера.", mr=job.kind == "mr")
                mark_handled(gl, state, job)
        save()
    return jobs


def sync_agent_config(home: Path, pull: bool = True) -> None:
    """Pick up agent/ (CLAUDE.md, mcp.json, skills) from gitlab-agent main. Worker code changes only via deploy."""
    src = home / "src" / "gitlab-agent"   # outside work/: the agent cannot write here
    if pull:
        env = runner.git_env()
        if (src / ".git").exists():
            subprocess.run(["git", "-C", str(src), "pull", "-q", "--ff-only"], env=env, capture_output=True, timeout=120)
        else:
            src.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", f"https://gitlab.com/{AGENT_WORKER}.git", str(src)], env=env,
                           capture_output=True, timeout=300)
    if not (src / "agent").is_dir():
        return
    dst = home / "app" / "agent"
    set_writable(dst, True)
    shutil.copytree(src / "agent", dst, dirs_exist_ok=True)
    set_writable(dst, False)
    if (src / "agent" / "CLAUDE.md").exists():
        (home / "work").mkdir(parents=True, exist_ok=True)
        target = home / "work" / "CLAUDE.md"
        set_writable(target, True)
        shutil.copyfile(src / "agent" / "CLAUDE.md", target)
        set_writable(target, False)


def set_writable(path: Path, on: bool) -> None:
    """Toggle the owner's write bit on a file or a whole tree (the agent runs as the same user)."""
    if not path.exists():
        return
    paths = [path] + (list(path.rglob("*")) if path.is_dir() else [])
    for p in sorted(paths, key=lambda x: len(x.parts), reverse=not on):
        if p.is_symlink():
            continue
        mode = p.stat().st_mode
        p.chmod(mode | 0o200 if on else mode & ~0o222)


def main(home: Path | None = None, gl=None, run_fn=None) -> str:
    home = home or default_home()
    if (home / "STOP").exists():
        return "stopped"
    (home / "state").mkdir(parents=True, exist_ok=True)
    lock = open(home / "state" / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return "busy"
    bot = config.bot_name()
    group = config.group()
    gl = gl or GitLab(token(), group)
    dry = os.environ.get("GITLAB_AGENT_DRY") == "1"
    if run_fn is None:
        sync_agent_config(home)
        run_fn = lambda job, h, ctx: runner.run(job, h, ctx)  # noqa: E731
    path = home / "state" / "state.json"
    state = State.load(path)
    if not dry:
        check_lint(gl, state, bot)
        recurring.run(gl, state, recurring.load(home / "app" / "agent" / "recurring.json"), datetime.now())
    tick(gl, state, home, run_fn, date.today(), bot, group, dry=dry, save=lambda: state.save(path))
    state.save(path)
    return "ok"


if __name__ == "__main__":
    print(main())
    sys.exit(0)
