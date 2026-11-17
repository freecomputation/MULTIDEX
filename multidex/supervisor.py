"""The desk supervisor: restart once, then bench (design section 13).

Owns one persistent worker subprocess per desk. A crash is a dead
process or a whole-job timeout. The first crash in an hour of watch
time earns a restart and one retry; the second benches the desk for
the watch, and the benching is reported so the Watch Officer can file
it as an event and the products can carry the coverage consequence.

The job timeout applies to the whole job, never per output line:
desk workers write only when their process() returns, so a per-line
timeout benches any desk whose honest work takes longer than the
timeout to produce its first line. The supervisor polls the result
queue on short ticks against one whole-job deadline, and a worker
process that dies is a crash the moment it is seen, not at the
deadline. Each worker's stderr is captured to a per-desk log in the
watch folder, so a benching carries the desk's own last words.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

_BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Whole-job budget in seconds. Generous on purpose: it exists to
# catch a hung desk, never to judge a slow one.
JOB_TIMEOUT_S = 600.0
_POLL_S = 0.05


class DeskCrash(Exception):
    pass


class _Worker:
    def __init__(self, name, stderr_path=None):
        self.name = name
        env = dict(os.environ)
        env["PYTHONPATH"] = _BUILD_ROOT + os.pathsep + env.get(
            "PYTHONPATH", "")
        self._stderr_fh = None
        stderr = subprocess.DEVNULL
        if stderr_path:
            try:
                os.makedirs(os.path.dirname(stderr_path),
                            exist_ok=True)
                self._stderr_fh = open(stderr_path, "a", buffering=1)
                stderr = self._stderr_fh
            except OSError:
                stderr = subprocess.DEVNULL
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "multidex.deskworker", name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr, text=True, env=env)
        self.lines = queue.Queue()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        # EOF: the worker exited. Wake any waiting job at once so a
        # crash is seen immediately, never as a full timeout.
        self.lines.put(None)

    def run(self, job_id, feed, ctx, poll_s=_POLL_S):
        if self.proc.poll() is not None:
            raise DeskCrash("worker already exited")
        try:
            self.proc.stdin.write(json.dumps(
                {"job_id": job_id, "feed": feed, "ctx": ctx}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise DeskCrash("worker pipe broke: %s" % exc)
        events = []
        deadline = time.monotonic() + JOB_TIMEOUT_S
        while True:
            try:
                line = self.lines.get(timeout=poll_s)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise DeskCrash("worker exited mid-job (crash)")
                if time.monotonic() >= deadline:
                    raise DeskCrash(
                        "worker produced no result within %.0f s "
                        "(hang treated as crash)" % JOB_TIMEOUT_S)
                continue
            if line is None:
                raise DeskCrash("worker exited mid-job (crash)")
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("__end__") == job_id:
                return events
            events.append(obj)

    def kill(self):
        try:
            self.proc.kill()
        except OSError:
            pass
        if self._stderr_fh is not None:
            try:
                self._stderr_fh.close()
            except OSError:
                pass
            self._stderr_fh = None


class Supervisor:
    def __init__(self):
        self.workers = {}
        self.crashes = {}   # desk -> [watch epochs of crashes]
        self.benched = {}   # desk -> reason
        self.job_counter = 0
        self.watch_dir = None

    def set_watch_dir(self, path):
        """Where per-desk worker stderr logs live for this watch."""
        self.watch_dir = path

    def _stderr_path(self, name):
        if not self.watch_dir:
            return None
        return os.path.join(self.watch_dir,
                            "desk-stderr-%s.log" % name)

    def _stderr_tail(self, name, limit=800):
        path = self._stderr_path(name)
        if not path:
            return None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return None
        text = text.strip()
        return text[-limit:] if text else None

    def _worker(self, name):
        worker = self.workers.get(name)
        if worker is None:
            worker = _Worker(name, self._stderr_path(name))
            self.workers[name] = worker
        return worker

    def _crashes_in_hour(self, name, t):
        return [c for c in self.crashes.get(name, []) if t - c < 3600]

    def _bench(self, name):
        reason = "crashed twice within an hour of watch time"
        self.benched[name] = reason
        info = {"benched": True, "reason": reason, "crashes": 2}
        tail = self._stderr_tail(name)
        if tail:
            info["stderr_tail"] = tail
        return [], info

    def run(self, name, feed, ctx):
        """Returns (events, info). info reports crashes and benching."""
        if name in self.benched:
            info = {"benched": True, "reason": self.benched[name],
                    "crashes": len(self.crashes.get(name, []))}
            tail = self._stderr_tail(name)
            if tail:
                info["stderr_tail"] = tail
            return [], info
        t = ctx.get("t0", 0)
        self.job_counter += 1
        try:
            events = self._worker(name).run(self.job_counter, feed, ctx)
            return events, {"crashes": 0}
        except DeskCrash:
            pass
        # First crash: record, restart, retry once.
        self.crashes.setdefault(name, []).append(t)
        old = self.workers.pop(name, None)
        if old is not None:
            old.kill()
        if len(self._crashes_in_hour(name, t)) >= 2:
            return self._bench(name)
        self.job_counter += 1
        try:
            events = self._worker(name).run(self.job_counter, feed, ctx)
            return events, {"crashes": 1, "restarted": True}
        except DeskCrash:
            self.crashes[name].append(t)
            old = self.workers.pop(name, None)
            if old is not None:
                old.kill()
            return self._bench(name)

    def is_benched(self, name):
        return name in self.benched

    def shutdown(self):
        for worker in self.workers.values():
            worker.kill()
        self.workers.clear()
