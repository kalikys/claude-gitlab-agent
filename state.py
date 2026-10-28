"""Worker state kept between runs (JSON, written atomically)."""
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class State:
    seen: dict = field(default_factory=dict)            # "<project_id>:<kind>:<iid>" -> last handled note id
    approved_done: list = field(default_factory=list)   # label event ids already written to the base
    failures: dict = field(default_factory=dict)        # job key -> failed runs in a row
    runs: dict = field(default_factory=dict)            # "YYYY-MM-DD" -> Claude runs that day
    lint_done: list = field(default_factory=list)       # main SHAs whose failed lint already has an issue
    inbox_waiting: dict = field(default_factory=dict)   # inbox file -> iid of the question issue
    reminded: list = field(default_factory=list)        # kb-review issue keys already reminded
    escalated: list = field(default_factory=list)       # person issues already escalated to the parent task
    cost: dict = field(default_factory=dict)            # "YYYY-MM-DD" -> estimated USD spent by Claude runs
    journal_iid: int = 0                                # gitlab-agent issue that collects daily reports
    reported: list = field(default_factory=list)        # days already reported
    recurring_done: dict = field(default_factory=dict)  # recurring task id -> period key last created
    due_nudged: list = field(default_factory=list)
    known_members: list = field(default_factory=list)   # group members seen so far (to notice who joined)      # "<project_id>:<iid>:<due date>" already nudged

    @classmethod
    def load(cls, path: Path) -> "State":
        if not Path(path).exists():
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
