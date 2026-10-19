"""Job store (SQLite) and a single worker that runs one generation job at a time."""
import asyncio
import json
import sqlite3
import time
import uuid
from typing import Callable

from config import DB_PATH, OUTPUTS_DIR

STATES = ("queued", "running", "done", "failed", "cancelled")


class JobStore:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, prompt TEXT, settings TEXT, state TEXT,
                stage TEXT, progress REAL, error TEXT, result TEXT,
                created REAL, updated REAL)"""
        )
        self.db.commit()

    def create(self, prompt: str, settings: dict) -> dict:
        now = time.time()
        job = dict(id=uuid.uuid4().hex[:12], prompt=prompt, settings=json.dumps(settings),
                   state="queued", stage="", progress=0.0, error=None, result=None,
                   created=now, updated=now)
        self.db.execute(f"INSERT INTO jobs VALUES ({','.join('?' * len(job))})", tuple(job.values()))
        self.db.commit()
        return self.get(job["id"])

    def update(self, job_id: str, **fields) -> dict:
        fields["updated"] = time.time()
        if "result" in fields and not isinstance(fields["result"], (str, type(None))):
            fields["result"] = json.dumps(fields["result"])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
        self.db.commit()
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_dict(row) if row else None

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["settings"] = json.loads(d["settings"] or "{}")
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d


class JobRunner:
    """Runs queued jobs sequentially; publishes every state change to subscribers."""

    def __init__(self, store: JobStore, pipeline: Callable):
        self.store = store
        self.pipeline = pipeline  # async fn(job, job_dir, report) -> result dict
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.subscribers: dict[str, set[asyncio.Queue]] = {}

    def submit(self, prompt: str, settings: dict) -> dict:
        job = self.store.create(prompt, settings)
        self.queue.put_nowait(job["id"])
        return job

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
            job_id = await self.queue.get()
            job = self.store.get(job_id)
            if not job or job["state"] == "cancelled":
                continue
            job_dir = OUTPUTS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)

            def report(stage: str, progress: float, _id=job_id, **extra):
                self._update(_id, stage=stage, progress=round(progress, 3), **extra)

            self._update(job_id, state="running")
            try:
                result = await self.pipeline(job, job_dir, report)
                final = dict(state="done", progress=1.0, result=result)
            except Exception as e:  # surface every failure to the UI
                final = dict(state="failed", error=str(e))
            if self.store.get(job_id)["state"] != "cancelled":
                self._update(job_id, **final)
