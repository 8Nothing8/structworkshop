"""Tiny background job runner for long tasks (preview render / batch import).

任务可以提交到**通道**（channel）：同一通道上的任务按 FIFO **排队串行**执行，
所以「渲染队列」只需要把每个渲染任务都提交到 ``preview`` 通道。

任务若用 ``report=True`` 提交，会收到一个 :class:`Progress` 句柄，用来回写
``log``（日志行）与 ``progress``（done/total/label），供前端进度条与队列面板显示。
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque


class Progress:
    """给任务用的进度句柄：``log(行)`` 追加日志，``set(done, total, label)`` 更新进度。"""

    __slots__ = ("_runner", "jid")

    def __init__(self, runner: "JobRunner", jid: str):
        self._runner = runner
        self.jid = jid

    def log(self, line: str) -> None:
        self._runner.log(self.jid, line)

    def set(self, done: int, total: int, label: str = "") -> None:
        self._runner.set_progress(self.jid, done, total, label)


class JobRunner:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._queues: dict[str, deque] = {}
        self._workers: dict[str, bool] = {}

    # ------------------------------------------------------------ submit
    def submit(self, name: str, fn, *args, channel: str | None = None,
               report: bool = False, **kwargs) -> str:
        """提交任务，返回 job id。

        ``channel`` 非空时任务进入该通道队列（同通道串行、先到先跑）；
        ``report=True`` 时 fn 的第一个参数是 :class:`Progress`。
        """
        with self._lock:
            self._seq += 1
            jid = f"job{self._seq}"
            self._jobs[jid] = {
                "id": jid, "name": name,
                "state": "queued" if channel else "running",
                "channel": channel,
                "started": time.time(), "finished": None,
                "log": [], "progress": None, "result": None, "error": None,
            }
            task = (jid, fn, args, kwargs, report)
            if not channel:
                threading.Thread(target=self._run_once, args=task,
                                 daemon=True).start()
            else:
                self._queues.setdefault(channel, deque()).append(task)
                if not self._workers.get(channel):
                    self._workers[channel] = True
                    threading.Thread(target=self._drain, args=(channel,),
                                     daemon=True).start()
        return jid

    # ------------------------------------------------------------ queue
    def _drain(self, channel: str) -> None:
        """通道工作线程：一次跑一个任务，跑完再取下一个。"""
        while True:
            with self._lock:
                q = self._queues.get(channel)
                if not q:
                    self._workers[channel] = False
                    return
                jid, fn, args, kwargs, report = q.popleft()
                job = self._jobs.get(jid)
                if job is None:  # 已被 cancel
                    continue
                job["state"] = "running"
                job["started"] = time.time()
            self._run_once(jid, fn, args, kwargs, report)

    def cancel(self, jid: str) -> dict | None:
        """取消**还在排队**的任务；运行中的任务返回原状态（调用方自行提示）。"""
        with self._lock:
            job = self._jobs.get(jid)
            if job is None:
                return None
            if job["state"] == "queued":
                q = self._queues.get(job.get("channel") or "")
                if q is not None:
                    for i, task in enumerate(q):
                        if task[0] == jid:
                            del q[i]
                            break
                job["state"] = "canceled"
                job["finished"] = time.time()
            return self._copy(job)

    # ------------------------------------------------------------ run
    def _run_once(self, jid, fn, args, kwargs, report) -> None:
        try:
            if report:
                result = fn(Progress(self, jid), *args, **kwargs)
            else:
                result = fn(*args, **kwargs)
            self._finish(jid, "done", result=result)
        except Exception as e:  # noqa: BLE001
            self._finish(jid, "error", error=f"{e}\n{traceback.format_exc()}")

    def _finish(self, jid, state, result=None, error=None) -> None:
        with self._lock:
            job = self._jobs.get(jid)
            if job is None or job["state"] == "canceled":
                return
            job["state"] = state
            job["result"] = result
            job["error"] = error
            job["finished"] = time.time()

    def log(self, jid: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(jid)
            if job is not None:
                job["log"].append(str(line))

    def set_progress(self, jid: str, done: int, total: int,
                     label: str = "") -> None:
        with self._lock:
            job = self._jobs.get(jid)
            if job is not None:
                job["progress"] = {"done": int(done), "total": int(total),
                                   "label": str(label or "")}

    # ------------------------------------------------------------ read
    @staticmethod
    def _copy(job: dict) -> dict:
        d = dict(job)
        d["log"] = list(job.get("log") or [])
        d["progress"] = dict(job["progress"]) if job.get("progress") else None
        return d

    def _ahead(self, jid: str) -> int:
        """同通道里排在该任务前面的数量（含正在跑的那个；调用方需持锁）。"""
        job = self._jobs.get(jid) or {}
        channel = job.get("channel") or ""
        busy = 1 if any(j.get("channel") == channel and j["state"] == "running"
                        for j in self._jobs.values()) else 0
        for i, task in enumerate(self._queues.get(channel) or ()):
            if task[0] == jid:
                return i + busy
        return busy

    def get(self, jid: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(jid)
            if job is None:
                return None
            d = self._copy(job)
            if d["state"] == "queued":
                d["queued_ahead"] = self._ahead(jid)
            return d

    def list(self) -> list[dict]:
        with self._lock:
            out = []
            for j in self._jobs.values():
                d = self._copy(j)
                if d["state"] == "queued":
                    d["queued_ahead"] = self._ahead(d["id"])
                out.append(d)
            return out[-20:]
