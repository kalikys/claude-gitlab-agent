#!/usr/bin/env python3
"""Scheduled jobs: `schedule.py inbox` (daily 20:00) and `schedule.py weekly` (Mondays 09:00)."""
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import config
import gl as glcli
import runner
from agent_secrets import token
from gitlab import GitLab
from poll import default_home, notes_of, sync_agent_config
from state import State
from triggers import Job

KB_PROJECT = config.kb_project()
REMIND_DAYS, ARCHIVE_DAYS = 14, 28
NOREPLY = re.compile(r"^\d+-(.+)@users\.noreply\.gitlab\.com$")
DOC_PATH = re.compile(r"[\w./-]+\.md")


def username_from(email: str, name: str) -> str:
    m = NOREPLY.match(email or "")
    return m.group(1) if m else name


def to_date(s: str) -> date:
    return date.fromisoformat(s[:10])


# ---------- inbox ----------

def inbox_files(kb: Path) -> list[str]:
    base = kb / "inbox"
    if not base.is_dir():
        return []
    return sorted(p.relative_to(kb).as_posix() for p in base.rglob("*") if p.is_file() and p.name != "README.md")


def note_author(kb: Path, rel: str) -> tuple[str, str]:
    out = subprocess.run(["git", "-C", str(kb), "log", "--diff-filter=A", "-1", "--format=%H|%ae|%an", "--", rel],
                         env=glcli.safe_git_env(), capture_output=True, text=True).stdout.strip()
    sha, email, name = (out.split("|") + ["", "", ""])[:3]
    return sha, username_from(email, name)


def parse_waiting(summary: str) -> dict:
    for line in reversed(summary.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and '"waiting"' in line:
            try:
                return {k: int(v) for k, v in json.loads(line).get("waiting", {}).items()}
            except (ValueError, AttributeError):
                return {}
    return {}


def run_inbox(gl, state: State, home: Path, run_fn, kb_project: str = KB_PROJECT, files_fn=None, author_fn=None):
    kb = home / "work" / "kb"
    files = (files_fn or (lambda: inbox_files(kb)))()
    author_fn = author_fn or (lambda f: note_author(kb, f))
    pid = gl.project_id(kb_project)
    for f, iid in list(state.inbox_waiting.items()):
        if f not in files or gl.get(f"projects/{pid}/issues/{iid}").get("state") == "closed":
            del state.inbox_waiting[f]
    pending = [f for f in files if f not in state.inbox_waiting]
    if not pending:
        return None
    lines = []
    for f in pending:
        sha, who = author_fn(f)
        lines.append(f"- {f}  автор: @{who}  коммит: https://gitlab.com/{kb_project}/-/commit/{sha}")
    job = Job("inbox", kb_project, None, f"{len(pending)} заметок в inbox")
    res = run_fn(job, home, "\n".join(lines))
    if res.ok:
        state.inbox_waiting.update(parse_waiting(res.summary))
    return res


# ---------- reminders for people (weekdays 10:00) ----------

REMINDER_MARK = "<!-- claude-reminder -->"
WAIT_DAYS, MAX_REMINDERS = 2, 2
PARENT = re.compile(r"Родительская задача:\s*https://gitlab\.com/(\S+?)/-/(?:issues|work_items)/(\d+)")


def working_days(start: date, end: date) -> int:
    """Weekdays after start up to and including end."""
    days, d = 0, start
    while d < end:
        d = date.fromordinal(d.toordinal() + 1)
        if d.weekday() < 5:
            days += 1
    return days


def run_reminders(gl, state: State, today: date, bot: str, group: str | None = None) -> list[str]:
    """Issues labelled claude-waiting wait for their assignee's answer. Remind twice, 2 working days apart,
    then tell the author of the parent task. Plain code, no Claude run."""
    group = group or config.group()
    report = []
    for it in gl.get(f"groups/{group}/issues", state="opened", labels="claude-waiting"):
        person = next((a["username"] for a in it.get("assignees", []) if a["username"] != bot), None)
        if not person:
            continue
        pid, iid = it["project_id"], it["iid"]
        notes = [n for n in notes_of(gl, pid, "issue", iid) if not n.get("system")]
        last_person = max((n["created_at"] for n in notes if n["author"]["username"] == person), default="")
        bot_after = [n for n in notes if n["author"]["username"] == bot and n["created_at"] > last_person]
        if not bot_after:
            continue
        reminders = [n for n in bot_after if REMINDER_MARK in n["body"]]
        last_bot = to_date(max(n["created_at"] for n in bot_after))
        if working_days(last_bot, today) < WAIT_DAYS:
            continue
        if len(reminders) < MAX_REMINDERS:
            gl.post(f"projects/{pid}/issues/{iid}/notes", {"body": (
                f"{REMINDER_MARK}\n@{person}, напоминаю про вопросы выше. Ответьте, пожалуйста, когда будет минутка: "
                "можно коротко, по пунктам. Если вопрос не к вам или нужно больше времени, так и напишите.")})
            report.append(f"напоминание @{person}: {it.get('web_url', '')}")
            continue
        key = f"{pid}:{iid}"
        m = PARENT.search(it.get("description") or "")
        if key in state.escalated or not m:
            continue
        ppid = gl.project_id(m.group(1))
        parent = gl.get(f"projects/{ppid}/issues/{m.group(2)}")
        owner = (parent.get("author") or {}).get("username", "")
        gl.post(f"projects/{ppid}/issues/{m.group(2)}/notes", {"body": (
            f"@{owner} @{person} не ответил(а) на вопросы ({it.get('web_url', '')}) после двух напоминаний. "
            "Продолжать без этих ответов или подождать? Ответьте здесь.")})
        gl.put(f"projects/{pid}/issues/{iid}", {"add_labels": "claude-no-reply", "remove_labels": "claude-waiting"})
        state.escalated.append(key)
        report.append(f"нет ответа от @{person}, сообщено @{owner}")
    return report


# ---------- due dates (weekdays 10:00, with reminders) ----------

DUE_SOON_DAYS = 2
OPEN_ITEM = re.compile(r"^\s*[-*] \[ \] (.+)$", re.M)


def run_due(gl, state: State, today: date, bot: str, group: str | None = None) -> list[str]:
    """Issues Claude works on (label claude): nudge the responsible person 2 days before the due date and once
    when it has passed, listing the open checklist items. Plain code."""
    group = group or config.group()
    report = []
    for it in gl.get(f"groups/{group}/issues", state="opened", labels="claude"):
        if not it.get("due_date"):
            continue
        due = date.fromisoformat(it["due_date"])
        left = (due - today).days
        if left > DUE_SOON_DAYS:
            continue
        stage = "overdue" if left < 0 else "soon"
        key = f"{it['project_id']}:{it['iid']}:{it['due_date']}:{stage}"
        if key in state.due_nudged:
            continue
        people = [a["username"] for a in it.get("assignees", []) if a["username"] != bot] or [it["author"]["username"]]
        items = OPEN_ITEM.findall(it.get("description") or "")
        when = f"просрочен на {-left} дн." if left < 0 else ("сегодня" if left == 0 else f"через {left} дн.")
        lines = [f"{' '.join('@' + p for p in people)} срок задачи {it['due_date']} — {when}."]
        if items:
            lines.append(f"Открытых пунктов чек-листа: {len(items)}: " + "; ".join(i.strip() for i in items[:5]) + ".")
        lines.append("Нужна помощь или стоит сдвинуть срок — напишите @" + bot + ".")
        gl.post(f"projects/{it['project_id']}/issues/{it['iid']}/notes", {"body": " ".join(lines)})
        state.due_nudged.append(key)
        report.append(f"срок {it['due_date']}: {it.get('web_url', '')}")
    return report


# ---------- daily report (21:00) ----------

AGENT_WORKER = config.worker_project()


def run_report(gl, state: State, home: Path, today: date, budget: float, owner: str | None = None) -> str:
    """Post today's runs, cost and stuck jobs to the 'Журнал агента' issue in gitlab-agent. Plain code."""
    owner = owner or config.owner()
    log = home / "logs" / f"{today.isoformat()}.jsonl"
    runs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.exists() else []
    cost = sum(r.get("cost_usd") or 0 for r in runs)
    failed = [r for r in runs if not r.get("ok")]
    stuck = [k for k, v in state.failures.items() if v >= 3]
    pid = gl.project_id(AGENT_WORKER)
    if not state.journal_iid:
        it = gl.post(f"projects/{pid}/issues", {
            "title": "Журнал агента", "assignee_ids": [],
            "description": f"Сюда воркер каждый вечер пишет отчёт: запуски, расход, сбои. @{owner} получает уведомления."})
        state.journal_iid = it["iid"]
    lines = [f"**Отчёт за {today.isoformat()}**", "",
             f"- Запусков: {len(runs)} (успешно {len(runs) - len(failed)}, сбоев {len(failed)})",
             f"- Расход: ${cost:.2f} из дневного лимита ${budget:.0f} (оценка по ценам API)"]
    jobs = sorted({r.get("job", "?") for r in runs})
    if jobs:
        lines.append("- Задачи: " + ", ".join(f"`{j}`" for j in jobs))
    if failed:
        lines.append("- Сбои: " + "; ".join(f"`{r.get('job')}` ({str(r.get('summary', ''))[:80]})" for r in failed))
    if stuck:
        lines.append(f"- @{owner} застряли после 3 попыток: " + ", ".join(f"`{k}`" for k in stuck))
    if not runs:
        lines = [f"**Отчёт за {today.isoformat()}**: запусков не было."]
    body = "\n".join(lines)
    gl.post(f"projects/{pid}/issues/{state.journal_iid}/notes", {"body": body})
    return body


# ---------- weekly ----------

def archive_doc(home: Path, doc: str, reason: str, source_url: str) -> bool:
    """Runs before any agent job of the weekly run, on the fresh clone made at start."""
    kb = home / "work" / "kb"
    r = subprocess.run([sys.executable, str(home / "app" / "kbtool" / "kb.py"), "archive", doc, "--reason", reason],
                       cwd=kb, env=glcli.safe_git_env(), capture_output=True, text=True)
    if r.returncode != 0:
        return False
    try:
        glcli._git("add", "-A", cwd=kb)
        glcli._git("-c", f"user.name={runner.BOT_NAME}", "-c", f"user.email={runner.BOT_EMAIL}", "commit", "-q", "-m",
                   f"kb: в архив {doc}\n\n{reason}\n\nSource: {source_url}", cwd=kb)
        glcli.push_repo(str(kb), KB_PROJECT, "main")
    except (RuntimeError, ValueError):
        return False
    return True


def stale_list(home: Path) -> list:
    r = subprocess.run([sys.executable, str(home / "app" / "kbtool" / "kb.py"), "stale", "--json"],
                       cwd=home / "work" / "kb", env=dict(os.environ, GITLAB_TOKEN=token()),
                       capture_output=True, text=True, timeout=600)
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return []


def handle_reviews(gl, state: State, today: date, kb_project: str, archive_fn, bot: str) -> list[str]:
    pid = gl.project_id(kb_project)
    report = []
    for it in gl.get(f"projects/{pid}/issues", state="opened", labels="kb-review"):
        if (it.get("author") or {}).get("username") != bot:
            continue  # only review questions the bot opened itself can lead to archiving
        owner = (it.get("assignees") or [{}])[0].get("username")
        created = to_date(it["created_at"])
        replied = any(n["author"]["username"] == owner and not n.get("system")
                      for n in notes_of(gl, pid, "issue", it["iid"]))
        if not owner or replied:
            continue
        days, key = (today - created).days, f"{pid}:{it['iid']}"
        if days >= ARCHIVE_DAYS:
            m = DOC_PATH.search(it.get("description") or "")
            if m and archive_fn(m.group(0), f"нет ответа владельца {ARCHIVE_DAYS} дней", it.get("web_url", "")):
                glcli.comment(gl, kb_project, it["iid"],
                              f"Ответа не было {ARCHIVE_DAYS} дней, документ `{m.group(0)}` перенесён в архив. "
                              "Если он ещё нужен, верните его из `archive/`.")
                gl.put(f"projects/{pid}/issues/{it['iid']}", {"state_event": "close"})
                report.append(f"в архив: {m.group(0)}")
        elif days >= REMIND_DAYS and key not in state.reminded:
            glcli.comment(gl, kb_project, it["iid"],
                          f"@{owner} напоминаю: документ ждёт проверки {days} дней. "
                          f"Если ответа не будет ещё {ARCHIVE_DAYS - days} дней, он уйдёт в архив.")
            state.reminded.append(key)
            report.append(f"напоминание @{owner}: {it.get('web_url', '')}")
    return report


def run_weekly(gl, state: State, home: Path, run_fn, today: date, bot: str, kb_project: str = KB_PROJECT,
               stale_fn=None, archive_fn=None):
    archive_fn = archive_fn or (lambda doc, reason, url: archive_doc(home, doc, reason, url))
    stale_fn = stale_fn or (lambda: stale_list(home))
    report = handle_reviews(gl, state, today, kb_project, archive_fn, bot)
    stale = stale_fn()
    if stale:
        run_fn(Job("stale", kb_project, None, f"{len(stale)} документов на проверку"), home,
               json.dumps(stale, ensure_ascii=False, indent=1))
    ctx = ["Действия воркера на этой неделе:"] + [f"- {r}" for r in report or ["нет"]]
    ctx.append(f"Заметки inbox, ждущие ответа: {len(state.inbox_waiting)}")
    run_fn(Job("digest", kb_project, None, "недельная сводка"), home, "\n".join(ctx))


def main(kind: str) -> str:
    home = default_home()
    if (home / "STOP").exists():
        return "stopped"
    # Same lock as poll.py: one writer of state.json and of the work folders at a time. Wait for a running tick.
    (home / "state").mkdir(parents=True, exist_ok=True)
    lock = open(home / "state" / "lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    gl = GitLab(token(), config.group())
    sync_agent_config(home)
    runner.fresh_clones(home)
    path = home / "state" / "state.json"
    state = State.load(path)
    def run_fn(job, h, ctx):
        day = date.today().isoformat()
        if state.cost.get(day, 0.0) >= float(os.environ.get("DAILY_BUDGET_USD", "15")):
            return runner.RunResult(False, 0.0, None, "daily budget reached")
        res = runner.run(job, h, ctx)
        state.cost[day] = round(state.cost.get(day, 0.0) + runner.run_cost(res), 4)
        return res
    if kind == "inbox":
        run_inbox(gl, state, home, run_fn)
    elif kind == "report":
        run_report(gl, state, home, date.today(), float(os.environ.get("DAILY_BUDGET_USD", "15")))
    elif kind == "remind":
        bot = config.bot_name()
        run_reminders(gl, state, date.today(), bot)
        run_due(gl, state, date.today(), bot)
    elif kind == "weekly":
        run_weekly(gl, state, home, run_fn, date.today(), config.bot_name())
    else:
        return f"unknown kind {kind}"
    state.save(path)
    return "ok"


if __name__ == "__main__":
    print(main(sys.argv[1] if len(sys.argv) > 1 else ""))
