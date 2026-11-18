"""0.2.0 transcription triage: the gate, the budget, the hourly
rate, queue displacement, and honest accounting. A fake engine
stands in for the host whisper stack throughout."""

import time

from multidex.transcribe import TriageQueue, Worker


def _job(freq=121.1e6, grade="A", score=85, snr=20.0, dur=10.0):
    return {"snippet": "/tmp/x.wav", "freq_hz": freq,
            "grade": grade, "score": score, "snr_db": snr,
            "duration_s": dur, "t": 0.0, "emitter": "AUDIO:X"}


def test_gate_rejects_below_a_and_low_snr():
    q = TriageQueue()
    assert q.submit(_job(grade="B")) == "DROPPED-GATE"
    assert q.submit(_job(snr=10.0)) == "DROPPED-GATE"
    assert q.submit(_job()) == "QUEUED"
    assert q.counts["dropped_gate"] == 2


def test_budget_is_charged_at_the_measured_factor():
    q = TriageQueue(budget_s=200.0)
    # A 10 s snippet costs 190 CPU-s at 19x realtime.
    assert q.submit(_job()) == "QUEUED"
    job = q.next_job(now=time.time())
    assert job is not None
    assert q.budget_spent_s == 190.0
    assert q.submit(_job(freq=125.4e6)) == "DROPPED-BUDGET"
    assert q.counts["dropped_budget"] == 1


def test_hourly_rate_cap():
    q = TriageQueue()
    now = time.time()
    for i in range(3):
        assert q.submit(_job(freq=100e6 + i)) == "QUEUED"
        assert q.next_job(now=now) is not None
    assert q.submit(_job(freq=200e6)) == "QUEUED"
    assert q.next_job(now=now) is None      # 3 done this hour
    assert q.next_job(now=now + 3700) is not None


def test_diversity_outranks_repeats():
    q = TriageQueue()
    q.submit(_job(freq=121.1e6, score=80))
    q.submit(_job(freq=121.1e6, score=84))
    q.submit(_job(freq=133.75e6, score=82))
    first = q.next_job(now=time.time())
    assert first["freq_hz"] == 133.75e6     # new frequency bonus


def test_full_queue_displaces_only_at_one_and_a_half_x():
    q = TriageQueue()
    # Twenty jobs on one frequency: priorities 80 (first, with
    # the diversity bonus) and 60 (the repeats).
    for i in range(20):
        assert q.submit(_job(freq=100e6, score=60)) == "QUEUED"
    # A repeat-frequency challenger at 70 cannot displace the
    # lowest (60): 70 < 1.5 * 60.
    assert q.submit(_job(freq=100e6, score=70)) == "DROPPED-QUEUE"
    # A new frequency at 95 carries the bonus to 115 >= 90.
    assert q.submit(_job(freq=301e6, score=95)) == "QUEUED"
    assert q.counts["displaced"] == 1


class _FakeEngine:
    def transcribe(self, path):
        return {"text": "test transmission", "language": "en"}


def test_worker_round_trip():
    q = TriageQueue()
    results = []
    worker = Worker(q, _FakeEngine(),
                    lambda j, r, e: results.append((j, r, e)))
    worker.start()
    try:
        q.submit(_job())
        deadline = time.time() + 10
        while not results and time.time() < deadline:
            time.sleep(0.1)
    finally:
        worker.stop()
    assert results and results[0][1]["text"] == "test transmission"
    assert q.counts["transcribed"] == 1
