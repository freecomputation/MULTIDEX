"""LRPT decode: the bounded post-pass SatDump step (0.4.1).

A METEOR pass is recorded as raw IQ by the tuner and filed by
the satellite desk; the imagery decode is this module's job,
and it is deliberately not the desk's: decoding a full pass
takes minutes of CPU, and a decode must never hold the tuner
or block the bill. The officer submits each locked LRPT pass
here after its IQ is filed; one background worker runs the
jobs in order; every outcome - image, no image, failure,
timeout - comes back through the sink and files as an imagery
event beside the pass record, which stands either way.

The binding is arms-length, like the other toolbind programs:
SatDump (satdump, staged on the host) is invoked as a separate
process over the filed IQ file, never linked, never bundled.
Its offline contract, proven on this build's reference host
against a real METEOR-M2 recording (SatDump 1.2.2):

    satdump <pipeline> baseband <iq file> <output dir>
        --samplerate <Hz> --baseband_format cu8 --dc_block
        --satellite_number <M2|M2-2|M2-3|M2-4>

Two field facts shape the invocation. The satellite number is
always passed explicitly: left on Auto, SatDump 1.2.2 can fail
to identify the satellite and segfault in reprojection after
writing its channel images. And success is judged by the image
product on disk, not the exit code alone, for the same reason.
The pipelines differ by generation: METEOR-M2 and M2-2 fly
QPSK (meteor_m2_lrpt); M2-3 and M2-4 fly OQPSK
(meteor_m2-x_lrpt) - which is the pipeline a current watch
flies, and the one whose first live proof is the first locked
M2-4 pass, the M2 pipeline being the one a published sample
recording proves today.
"""

import glob
import os
import shutil
import subprocess
import tempfile
import threading

# A full pass at the LRPT capture rate decodes in well under a
# minute per pass-minute on the reference host; the bound is
# generous on purpose - it exists to catch a hung decoder,
# never to judge a slow one - and a timeout files its event
# like any other outcome.
DECODE_TIMEOUT_S = 1200.0

QUEUE_DEPTH = 8


def satdump_available():
    return shutil.which("satdump") is not None


def pipeline_for(sat_name):
    """(pipeline id, --satellite_number value) for a satellite."""
    sat = (sat_name or "").upper().replace("-", " ")
    if "M2 4" in sat:
        return "meteor_m2-x_lrpt", "M2-4"
    if "M2 3" in sat:
        return "meteor_m2-x_lrpt", "M2-3"
    if "M2 2" in sat:
        return "meteor_m2_lrpt", "M2-2"
    return "meteor_m2_lrpt", "M2"


# Product preference, best first: a corrected colour composite
# is the pass's headline image; a single channel is the honest
# fallback. Patterns are matched against the product filename.
_PRODUCT_PREFERENCE = (
    "MSA_corrected.png", "False_Color_corrected.png",
    "MSA.png", "False_Color.png", "MSU-MR-1.png")


def _pick_product(out_dir):
    pngs = [p for p in glob.glob(os.path.join(out_dir, "**", "*.png"),
                                 recursive=True)]
    if not pngs:
        return None
    for token in _PRODUCT_PREFERENCE:
        matches = [p for p in pngs if p.endswith(token)]
        if matches:
            return max(matches, key=os.path.getsize)
    return max(pngs, key=os.path.getsize)


def decode_iq(iq_path, rate, sat_name, timeout=DECODE_TIMEOUT_S):
    """Decode one filed LRPT IQ recording with SatDump.

    Returns (png_bytes, info) on success - info carries the
    pipeline, the satellite number, SatDump's exit code and the
    product filename - or (None, plain-word reason). A decode
    that produces no image is a result, never an exception.
    """
    tool = shutil.which("satdump")
    if tool is None:
        return None, "satdump is not staged on this host"
    pipeline, sat_number = pipeline_for(sat_name)
    with tempfile.TemporaryDirectory() as td:
        out_dir = os.path.join(td, "out")
        os.makedirs(out_dir)
        cmd = [tool, pipeline, "baseband", iq_path, out_dir,
               "--samplerate", str(int(rate)),
               "--baseband_format", "cu8", "--dc_block",
               "--satellite_number", sat_number]
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            return None, ("satdump did not finish within %d s; "
                          "the pass IQ stands as the evidence"
                          % int(timeout))
        except OSError as exc:
            return None, "satdump could not be run: %s" % exc
        product = _pick_product(out_dir)
        if product is None:
            if code != 0:
                return None, ("satdump exited %d and produced no "
                              "image from this pass IQ" % code)
            return None, ("satdump produced no image from this "
                          "pass IQ (no decodable LRPT frames in "
                          "the recording)")
        with open(product, "rb") as fh:
            png = fh.read()
    if not png.startswith(b"\x89PNG") or len(png) < 1000:
        return None, ("satdump's product was not a usable PNG "
                      "image")
    return png, {"pipeline": pipeline,
                 "satellite_number": sat_number,
                 "exit_code": code,
                 "product": os.path.basename(product)}


# ------------------------------------------------------------ the queue

class DecodeQueue:
    """A small honest queue: passes are rare, order is FIFO,
    and every drop is counted with its reason."""

    def __init__(self, depth=QUEUE_DEPTH):
        self._jobs = []
        self._lock = threading.Lock()
        self.depth = depth
        self.counts = {"submitted": 0, "decoded": 0, "failed": 0,
                       "dropped_queue": 0}
        self._in_flight = 0

    def submit(self, job):
        with self._lock:
            self.counts["submitted"] += 1
            if len(self._jobs) >= self.depth:
                self.counts["dropped_queue"] += 1
                return "DROPPED-QUEUE"
            self._jobs.append(job)
            return "QUEUED"

    def next_job(self):
        with self._lock:
            if not self._jobs:
                return None
            job = self._jobs.pop(0)
            self._in_flight += 1
            return job

    def complete(self, job, ok):
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self.counts["decoded" if ok else "failed"] += 1

    def pending(self):
        with self._lock:
            return len(self._jobs) + self._in_flight


class DecodeWorker:
    """Background consumer: one decode at a time, results handed
    back through the sink for the officer to file on its own
    thread - the same shape as the transcription worker."""

    def __init__(self, queue, sink):
        self.queue = queue
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
            self._thread.join(timeout=30)
            self._thread = None

    def _run(self):
        while not self._stop.is_set():
            job = self.queue.next_job()
            if job is None:
                self._stop.wait(1.0)
                continue
            png, info_or_reason = decode_iq(
                job["iq_path"], job.get("iq_rate") or 0,
                job.get("sat"))
            ok = png is not None
            # The sink takes the result before the job completes:
            # a drainer that sees the queue idle must already
            # find the result waiting.
            self.sink(job, png, info_or_reason)
            self.queue.complete(job, ok)
