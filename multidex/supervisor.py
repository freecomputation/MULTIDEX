"""The desk supervisor: restart once, then bench (design section 13).

Owns one persistent worker subprocess per desk. A crash is a dead
process or a job timeout. The first crash in an hour of watch time
earns a restart and one retry; the second benches the desk for the
watch, and the benching is reported so the Watch Officer can file it
as an event and the products can carry the coverage consequence.
"""

import json
import os
import queue
import subprocess
import sys
import threading

_BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB_TIMEOUT_S = 30.0


class DeskCrash(Exception):
    pass


class _Worker:
    def __init__(self, name):
        self.name = name
        env = dict(os.environ)
        env["PYTHONPATH"] = _BUILD_ROOT + os.pathsep + env.get(
            "PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "multidex.deskworker", name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=env)
        self.lines = queue.Queue()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        # EOF: the worker exited. Wake any waiting job at once so a
        # crash is seen immediately, never as a full timeout.
        self.lines.put(None)

    def run(self, job_id, feed, ctx, timeout=JOB_TIMEOUT_S):
        if self.proc.poll() is not None:
            raise DeskCrash("worker already exited")
        try:
            self.proc.stdin.write(json.dumps(
                {"job_id": job_id, "feed": feed, "ctx": ctx}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise DeskCrash("worker pipe broke: %s" % exc)
        events = []
        while True:
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                raise DeskCrash("worker timed out (hang treated as crash)")
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


class Supervisor:
    def __init__(self):
        self.workers = {}
        self.crashes = {}   # desk -> [watch epochs of crashes]
        self.benched = {}   # desk -> reason
        self.job_counter = 0

    def _worker(self, name):
        worker = self.workers.get(name)
        if worker is None:
            worker = _Worker(name)
            self.workers[name] = worker
        return worker

    def _crashes_in_hour(self, name, t):
        return [c for c in self.crashes.get(name, []) if t - c < 3600]

    def run(self, name, feed, ctx):
        """Returns (events, info). info reports crashes and benching."""
        if name in self.benched:
            return [], {"benched": True, "reason": self.benched[name],
                        "crashes": len(self.crashes.get(name, []))}
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
            reason = "crashed twice within an hour of watch time"
            self.benched[name] = reason
            return [], {"benched": True, "reason": reason, "crashes": 2}
        self.job_counter += 1
        try:
            events = self._worker(name).run(self.job_counter, feed, ctx)
            return events, {"crashes": 1, "restarted": True}
        except DeskCrash:
            self.crashes[name].append(t)
            old = self.workers.pop(name, None)
            if old is not None:
                old.kill()
            reason = "crashed twice within an hour of watch time"
            self.benched[name] = reason
            return [], {"benched": True, "reason": reason, "crashes": 2}

    def is_benched(self, name):
        return name in self.benched

    def shutdown(self):
        for worker in self.workers.values():
            worker.kill()
        self.workers.clear()
