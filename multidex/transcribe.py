"""Bounded transcription triage (0.2.0).

The watch host's faster-whisper stack runs about 19x slower
than realtime, so transcription is a rationed resource, never
an unbounded queue:

- Gate: census grade A only, RF SNR at least 15 dB. Everything
  else stays a filed snippet with its grade, untranscribed.
- Budget: 90 CPU-minutes per 24 h watch, estimated at 19x the
  snippet duration per job and charged before the job runs.
- Rate: at most 3 jobs per hour; queue depth at most 20.
- Diversity: first candidate from a frequency outranks repeats;
  a full queue displaces its lowest-priority job only for a
  candidate at 1.5x its priority.
- Every drop is accounted with its reason (DROPPED-GATE,
  DROPPED-QUEUE, DROPPED-BUDGET), so the report can state how
  many grade-A snippets went untranscribed and why.

Transcripts are machine transcripts: they inherit the snippet's
grade, are labelled unverified in every product, and never
upgrade the grade of the emission they came from.
"""

import threading
import time

CPU_BUDGET_S = 90 * 60.0
MAX_JOBS_PER_HOUR = 3
QUEUE_DEPTH = 20
REALTIME_FACTOR = 19.0     # measured on the watch host, small/int8
MIN_SNR_DB = 15.0


class TriageQueue:
    def __init__(self, budget_s=CPU_BUDGET_S):
        self._jobs = []            # pending job dicts
        self._lock = threading.Lock()
        self._seen_freqs = set()
        self._done_hour = []       # completion epochs, rolling hour
        self.budget_s = budget_s
        self.budget_spent_s = 0.0
        self.counts = {"submitted": 0, "accepted": 0,
                       "transcribed": 0, "dropped_gate": 0,
                       "dropped_queue": 0, "dropped_budget": 0,
                       "displaced": 0}

    # -------------------------------------------------- producer
    def submit(self, job):
        """job: {snippet, freq_hz, grade, score, snr_db,
        duration_s, t}. Returns a plain-word outcome."""
        with self._lock:
            self.counts["submitted"] += 1
            if job.get("grade") != "A" \
                    or job.get("snr_db", 0) < MIN_SNR_DB:
                self.counts["dropped_gate"] += 1
                return "DROPPED-GATE"
            cost = job.get("duration_s", 10.0) * REALTIME_FACTOR
            if self.budget_spent_s + cost > self.budget_s:
                self.counts["dropped_budget"] += 1
                return "DROPPED-BUDGET"
            priority = job.get("score", 0) + (
                20 if job["freq_hz"] not in self._seen_freqs else 0)
            job = dict(job, priority=priority, cost_s=cost)
            if len(self._jobs) >= QUEUE_DEPTH:
                lowest = min(self._jobs,
                             key=lambda j: j["priority"])
                if priority >= 1.5 * lowest["priority"]:
                    self._jobs.remove(lowest)
                    self.counts["displaced"] += 1
                else:
                    self.counts["dropped_queue"] += 1
                    return "DROPPED-QUEUE"
            self._seen_freqs.add(job["freq_hz"])
            self._jobs.append(job)
            self.counts["accepted"] += 1
            return "QUEUED"

    # -------------------------------------------------- consumer
    def next_job(self, now=None):
        """Pop the highest-priority job the hourly rate allows,
        charging its estimated cost. None when idle or capped."""
        now = time.time() if now is None else now
        with self._lock:
            self._done_hour = [t for t in self._done_hour
                               if now - t < 3600]
            if len(self._done_hour) >= MAX_JOBS_PER_HOUR:
                return None
            if not self._jobs:
                return None
            job = max(self._jobs, key=lambda j: j["priority"])
            self._jobs.remove(job)
            self.budget_spent_s += job["cost_s"]
            self._done_hour.append(now)
            return job

    def complete(self, job):
        with self._lock:
            self.counts["transcribed"] += 1

    def pending(self):
        with self._lock:
            return len(self._jobs)


# ------------------------------------------------------------ engine

class WhisperEngine:
    """The host's faster-whisper stack, lazily loaded. Loading
    failure is a state, not an exception: available() says."""

    def __init__(self, model="small"):
        self._model_name = model
        self._model = None
        self._error = None

    def available(self):
        if self._model is not None:
            return True
        if self._error is not None:
            return False
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self._model_name, device="cpu",
                compute_type="int8")
            return True
        except Exception as exc:
            self._error = "%s: %s" % (type(exc).__name__, exc)
            return False

    @property
    def error(self):
        return self._error

    def transcribe(self, path):
        if not self.available():
            raise RuntimeError(self._error or "engine unavailable")
        segments, info = self._model.transcribe(
            path, vad_filter=True, beam_size=1)
        text = " ".join(s.text.strip() for s in segments).strip()
        return {"text": text,
                "language": getattr(info, "language", None)}


class Worker:
    """Background consumer: pulls jobs, transcribes, hands
    results back through a callback the officer drains on its
    own thread. One job at a time - the host has two cores and
    the watch itself is using one."""

    def __init__(self, queue, engine, sink):
        self.queue = queue
        self.engine = engine
        self.sink = sink
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run,
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self):
        while not self._stop.is_set():
            job = self.queue.next_job()
            if job is None:
                self._stop.wait(2.0)
                continue
            try:
                result = self.engine.transcribe(job["snippet"])
                self.queue.complete(job)
                self.sink(job, result, None)
            except Exception as exc:
                self.sink(job, None, exc)
