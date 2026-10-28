"""Decide what needs the agent. Pure functions over GitLab API data."""
import re
from dataclasses import dataclass, field

from state import State


@dataclass
class Job:
    kind: str            # issue | mr | approved | inbox | stale | digest
    project: str         # full path, e.g. acme/kb
    iid: int | None
    reason: str
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.project}:{self.kind}:{self.iid}"


def project_path(web_url: str) -> str:
    m = re.match(r"https?://[^/]+/(.+?)/-/", web_url)
    return m.group(1) if m else ""


def seen_key(project_id: int, kind: str, iid: int) -> str:
    return f"{project_id}:{kind}:{iid}"


def human_note_after(notes, last: int, members: dict, bot: str) -> dict | None:
    for n in notes:
        who = n["author"]["username"]
        if n["id"] > last and not n.get("system") and who in members and who != bot:
            return n
    return None


def issue_jobs(issues, notes_by_issue, members, bot, state: State) -> list[Job]:
    jobs = []
    for it in issues:
        labels = it.get("labels", [])
        mine = bot in [a["username"] for a in it.get("assignees", [])] or "claude" in labels
        if not mine or "claude-pause" in labels:
            continue
        key = seen_key(it["project_id"], "issue", it["iid"])
        project = project_path(it["web_url"])
        if key not in state.seen:
            jobs.append(Job("issue", project, it["iid"], "новая задача", {"seen_key": key, "project_id": it["project_id"]}))
            continue
        n = human_note_after(notes_by_issue.get((it["project_id"], it["iid"]), []), state.seen[key], members, bot)
        if n:
            jobs.append(Job("issue", project, it["iid"], f"новый комментарий @{n['author']['username']}",
                            {"seen_key": key, "project_id": it["project_id"]}))
    return jobs


def mr_jobs(mrs, notes_by_mr, members, bot, state: State) -> list[Job]:
    jobs = []
    for mr in mrs:
        if "claude-pause" in mr.get("labels", []):
            continue
        key = seen_key(mr["project_id"], "mr", mr["iid"])
        n = human_note_after(notes_by_mr.get((mr["project_id"], mr["iid"]), []), state.seen.get(key, 0), members, bot)
        if n:
            jobs.append(Job("mr", project_path(mr["web_url"]), mr["iid"], f"ревью от @{n['author']['username']}",
                            {"seen_key": key, "project_id": mr["project_id"]}))
    return jobs


def mention_jobs(todos, members, bot, group) -> list[Job]:
    jobs = []
    for t in todos:
        if t.get("action_name") not in ("mentioned", "directly_addressed"):
            continue
        who = t.get("author", {}).get("username")
        if who == bot or who not in members:
            continue
        if not t.get("project", {}).get("path_with_namespace", "").lower().startswith(group.lower() + "/"):
            continue
        kind = {"Issue": "issue", "MergeRequest": "mr"}.get(t.get("target_type"))
        if not kind:
            continue
        jobs.append(Job(kind, t["project"]["path_with_namespace"], t["target"]["iid"],
                        f"упоминание от @{t.get('author', {}).get('username', '?')}", {"todo_ids": [t["id"]]}))
    return jobs


def approved_jobs(events_by_issue, members, bot, state: State) -> list[Job]:
    jobs = []
    for (project, iid), events in events_by_issue.items():
        for e in events:
            who = (e.get("user") or {}).get("username")
            if e.get("action") != "add" or (e.get("label") or {}).get("name") != "approved":
                continue
            if e["id"] in state.approved_done or who == bot or who not in members:
                continue
            jobs.append(Job("approved", project, iid, f"одобрено @{who}", {"approver": who, "event_id": e["id"],
                                                                           "approved_at": e.get("created_at", "")}))
    return jobs


def dedupe(jobs: list[Job]) -> list[Job]:
    out: dict[tuple, Job] = {}
    for j in jobs:
        k = (j.project, j.kind, j.iid)
        if k not in out:
            out[k] = Job(j.kind, j.project, j.iid, j.reason, dict(j.extra))
            continue
        cur = out[k]
        for key, val in j.extra.items():
            if isinstance(val, list):
                cur.extra[key] = cur.extra.get(key, []) + val
            else:
                cur.extra.setdefault(key, val)
    return list(out.values())
