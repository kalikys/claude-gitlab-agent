"""Recurring tasks from agent/recurring.json: the worker opens an issue (label claude) once per period.

Entry: {"id": "weekly-status", "project": "acme/<repo>", "title": "Статус недели {date}",
        "description": "...", "every": "mon".."sun" | "day" | "month:<1-28>", "at": "HH:MM",
        "assignee": "<username>" (optional), "labels": ["..."] (optional)}
The file changes only through a merge request into gitlab-agent, so a human approves every schedule.
"""
import json
from datetime import datetime
from pathlib import Path

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def load(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def period_key(every: str, now: datetime) -> str | None:
    """Key of the current period if the task is due today, else None."""
    if every == "day":
        return now.strftime("%Y-%m-%d")
    if every in DAYS:
        return now.strftime("%G-W%V") if now.weekday() == DAYS.index(every) else None
    if every.startswith("month:"):
        day = int(every.split(":", 1)[1])
        return now.strftime("%Y-%m") if now.day == day else None
    raise ValueError(f"bad 'every': {every}")


def run(gl, state, tasks: list[dict], now: datetime) -> list[str]:
    created = []
    for t in tasks:
        try:
            key = period_key(t["every"], now)
            hh, mm = (int(x) for x in t.get("at", "09:00").split(":"))
            if key is None or (now.hour, now.minute) < (hh, mm) or state.recurring_done.get(t["id"]) == key:
                continue
            pid = gl.project_id(t["project"])
            data = {"title": t["title"].replace("{date}", now.strftime("%Y-%m-%d")),
                    "description": t.get("description", "") + f"\n\n_Повторяющаяся задача `{t['id']}` ({t['every']})._",
                    "labels": ",".join(["claude"] + [l for l in t.get("labels", []) if l != "claude"])}
            if t.get("assignee"):
                users = gl.get("users", username=t["assignee"])
                if users:
                    data["assignee_ids"] = [users[0]["id"]]
            it = gl.post(f"projects/{pid}/issues", data)
            state.recurring_done[t["id"]] = key
            created.append(it.get("web_url", ""))
        except (KeyError, ValueError, TypeError) as e:
            print(f"recurring task {t.get('id', '?')} skipped: {e}")
    return created
