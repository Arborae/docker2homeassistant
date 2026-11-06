"""Background jobs (update, update all, prune) run one at a time, with live progress."""

import logging
import queue
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

MAX_JOBS = 30


class Job:
    def __init__(self, kind: str, target: str, label: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.target = target  # container key, or "" for global jobs
        self.label = label
        self.state = "queued"  # queued, running, done, failed
        self.phase = ""
        self.percent: Optional[int] = None
        self.current = 0
        self.total = 0
        self.detail = ""
        self.step = 0  # update all: index of the container being updated
        self.steps = 0
        self.result: Dict[str, Any] = {}
        self.error = ""
        self.created = time.time()
        self.finished = 0.0
        self._listeners: List[queue.Queue] = []
        self._lock = threading.Lock()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "target": self.target, "label": self.label,
            "state": self.state, "phase": self.phase, "percent": self.percent,
            "current": self.current, "total": self.total, "detail": self.detail,
            "step": self.step, "steps": self.steps, "result": self.result, "error": self.error,
            "created": self.created, "finished": self.finished,
        }

    def publish(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)
            snapshot = self.as_dict()
            listeners = list(self._listeners)
        for listener in listeners:
            listener.put(snapshot)

    def subscribe(self) -> "queue.Queue[Dict[str, Any]]":
        q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        with self._lock:
            self._listeners.append(q)
            q.put(self.as_dict())
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")


class JobRunner:
    def __init__(self):
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="jobs", daemon=True).start()

    def submit(self, kind: str, target: str, label: str, work: Callable[[Job], Dict[str, Any]]) -> Job:
        with self._lock:
            for job in self._jobs.values():
                if job.active and job.kind == kind and job.target == target:
                    return job  # the same work is already queued or running
            job = Job(kind, target, label)
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS:
                oldest = next(iter(self._jobs))
                if self._jobs[oldest].active:
                    break
                self._jobs.pop(oldest)
        self._queue.put((job, work))
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> List[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.active]

    def run_now(self, job: Job, work: Callable[[Job], Dict[str, Any]]) -> None:
        """Execute a job synchronously (used by the worker thread and by tests)."""
        job.publish(state="running")
        try:
            result = work(job) or {}
            job.publish(state="done", phase="done", percent=100, detail="", result=result, finished=time.time())
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the client
            code = getattr(exc, "code", "") or "error.generic"
            log.warning("Job %s (%s %s) failed: %s", job.id, job.kind, job.target, exc)
            job.publish(state="failed", error=code, detail=str(getattr(exc, "detail", "") or exc)[:300],
                        finished=time.time())

    def _loop(self) -> None:
        while True:
            job, work = self._queue.get()
            self.run_now(job, work)
