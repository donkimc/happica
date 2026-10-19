"""Job store (SQLite) and a single worker that runs one generation job at a time."""
import asyncio
import json
import shutil
import sqlite3
import time
import uuid
from typing import Callable

from config import DB_PATH, OUTPUTS_DIR

STATES = ("queued", "running", "done", "failed", "cancelled")
KINDS = ("video", "music", "sfx", "image", "voice", "character")


class JobStore:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, prompt TEXT, settings TEXT, state TEXT,
                stage TEXT, progress REAL, error TEXT, result TEXT,
                created REAL, updated REAL, kind TEXT NOT NULL DEFAULT 'video')"""
        )
        # Databases from before music existed: every job in them is a video job
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)")}
        if "kind" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'video'")
        self.db.commit()

    def create(self, prompt: str, settings: dict, kind: str = "video") -> dict:
        now = time.time()
        job = dict(id=uuid.uuid4().hex[:12], prompt=prompt, settings=json.dumps(settings),
                   state="queued", stage="", progress=0.0, error=None, result=None,
                   created=now, updated=now, kind=kind)
        self.db.execute(f"INSERT INTO jobs ({','.join(job)}) VALUES ({','.join('?' * len(job))})", tuple(job.values()))
        self.db.commit()
        return self.get(job["id"])

    def update(self, job_id: str, **fields) -> dict:
        fields["updated"] = time.time()
        for key in ("result", "settings"):
            if key in fields and not isinstance(fields[key], (str, type(None))):
                fields[key] = json.dumps(fields[key])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
        self.db.commit()
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_dict(row) if row else None

    def delete(self, job_id: str) -> None:
        self.db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.db.commit()

    def list(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        if kind:
            rows = self.db.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY created DESC LIMIT ?", (kind, limit)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["settings"] = json.loads(d["settings"] or "{}")
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d


Task = Callable  # async fn(job, job_dir, report) -> result dict


class JobRunner:
    """Runs queued tasks one at a time (the GPU only fits one); publishes every state change to subscribers.

    A task is either a full generation (`submit`, using the pipeline for the job's kind) or follow-up
    work on an existing job (`enqueue`), such as regenerating one scene.
    """

    def __init__(self, store: JobStore, pipelines: dict[str, Task]):
        self.store = store
        self.pipelines = pipelines
        self.queue: asyncio.Queue[tuple[str, Task]] = asyncio.Queue()
        self.subscribers: dict[str, set[asyncio.Queue]] = {}

    def submit(self, prompt: str, settings: dict, kind: str = "video") -> dict:
        job = self.store.create(prompt, settings, kind)
        self.queue.put_nowait((job["id"], self.pipelines[kind]))
        return job

    def enqueue(self, job_id: str, task: Task, stage: str = "") -> dict:
        job = self._update(job_id, state="queued", stage=stage, progress=0.0, error=None)
        self.queue.put_nowait((job_id, task))
        return job

    def delete(self, job_id: str) -> None:
        self.store.delete(job_id)
        job_dir = (OUTPUTS_DIR / job_id).resolve()
        if job_dir.parent == OUTPUTS_DIR.resolve() and job_dir.is_dir():
            shutil.rmtree(job_dir)

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        self.subscribers.get(job_id, set()).discard(q)

    def _publish(self, job: dict) -> None:
        for q in self.subscribers.get(job["id"], ()):
            q.put_nowait(job)

    def _update(self, job_id: str, **fields) -> dict:
        job = self.store.update(job_id, **fields)
        self._publish(job)
        return job

    async def run_forever(self) -> None:
        while True:
            job_id, task = await self.queue.get()
            job = self.store.get(job_id)
            if not job or job["state"] == "cancelled":  # deleted or cancelled while waiting
                continue
            job_dir = OUTPUTS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)

            def report(stage: str, progress: float, _id=job_id, **extra):
                self._update(_id, stage=stage, progress=round(progress, 3), **extra)

            self._update(job_id, state="running")
            try:
                result = await task(job, job_dir, report)
                final = dict(state="done", progress=1.0, result=result)
            except Exception as e:  # surface every failure to the UI
                final = dict(state="failed", error=str(e))
            current = self.store.get(job_id)
            if current and current["state"] != "cancelled":
                self._update(job_id, **final)
