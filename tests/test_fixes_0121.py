"""0.12.1: the ACARS conforming front end on the live paths.

The measured defect, found by the 0.12.0 build against a test
matrix and confirmed here on the pre-fix geometry: the frame
layer's parity gate reads a span of up to 240 bytes from a
frame's start and demands it overwhelmingly parity-clean, the
BCS pair aside, so bytes demodulated from whatever trails a
frame inside its slice sink the gate. Before this build the
three paths that feed the frame layer held three geometries:

- the tuner's live feed segmented on the smoothed envelope
  and sliced samples[i - 600 : j + 600] - the trailing 600
  samples are 120 bits, some fifteen bytes of demodulated
  noise inside the gate's span for a short frame, and the
  frame was discarded whole;
- the probe desk's ACARS attempt fed the census
  demodulator's whole-excerpt audio (a single global mean
  subtracted, leaving a baseline step at the burst's edges,
  and the frame trailing the excerpt's full padding) to the
  frame layer unsliced;
- the retroactive decoder held the one conforming copy -
  local-baseline demodulation and activity-edge slices -
  privately, where the live paths could not share it.

After this build the front end lives once, in multidex.acars
(am_demod, burst_slices, decode_sliced), and all three paths
call it: the slice is audio[max(0, i - 30) : j + 30] over the
same smoothed-envelope segment - a guard of half the
60-sample smoothing window, six bits at 2400 baud, under one
byte, which covers the envelope's edge softness (in heavy
noise its crossing sits a few samples inside the burst, and
the frame's last bits live there) at a trailing cost the
gate absorbs. The regression fixture below is a real tuner
capture's shape - one verified frame on a quiet noise bed -
and it decodes through the desk's live entry point
(RealSource._feed_acars into desks.process_acars) while the
pre-fix geometry, reproduced inline, decodes nothing.
"""

import base64
import inspect

import numpy as np

from multidex import acars, audiocensus, desks, retro, sigmf
from multidex.tuner import RealSource

from conftest import T0

RATE = 12000


# ------------------------------------------------------------ fixtures

def _frame_audio(text="OOOI 1234 FUEL 9200 KG", tail="N12345"):
    body = "001UA12 " + text
    frame = acars.build_frame_bytes("2", tail, " ", "H1", "A", body)
    return acars.bits_to_audio(acars.bytes_to_bits(frame), RATE)


def _capture(bursts, seconds=8.0, noise=0.02, seed=4):
    """A tuner capture's shape: a quiet noise bed with audio
    bursts laid into it, as rtl_fm delivers a datalink dwell."""
    rng = np.random.default_rng(seed)
    out = rng.normal(0, noise, int(RATE * seconds))
    for at_s, audio in bursts:
        at = int(at_s * RATE)
        out[at:at + len(audio)] += audio
    return out


def _live_feed(profile, samples):
    """The desk's live entry point minus the radio itself:
    RealSource._feed_acars with _audio stubbed to the capture,
    then desks.process_acars over the feed it returns."""
    source = RealSource(profile)
    source._audio = lambda freq, mode, rate, seconds, \
        gain="23": list(samples)
    feed = source._feed_acars([{"freq_hz": 131.55e6}], 131.55e6,
                              25e3, T0, T0 + 10)
    ctx = {"watch_id": "watch-t", "desk": "acars", "t0": T0,
           "t1": T0 + 10, "freq_hz": 131.55e6,
           "home": (profile["home"]["lat"],
                    profile["home"]["lon"]),
           "profile": profile, "pass": None}
    return feed, desks.process_acars(feed, ctx)


def _old_tuner_messages(samples):
    """The pre-0.12.1 live geometry, reproduced from the
    tuner code as it stood: smoothed-envelope segments sliced
    with 600 samples of margin at both ends, decoded when the
    margined slice exceeded 2400 samples."""
    env = np.abs(samples)
    env = np.convolve(env, np.ones(60) / 60.0, mode="same")
    floor = np.median(env) + 1e-9
    active = env > floor * 4.0
    messages = []
    i, n = 0, len(samples)
    while i < n:
        if active[i]:
            j = i
            while j < n and (active[j] or
                             (j + 1200 < n and active[j + 1200])):
                j += 1
            burst = samples[max(0, i - 600):min(n, j + 600)]
            if len(burst) > 2400:
                messages.extend(acars.decode_audio(burst, RATE))
            i = j
        else:
            i += 1
    return messages


# ------------------------------------------------------- one home

def test_front_end_lives_once_in_the_acars_module():
    assert callable(acars.am_demod)
    assert callable(acars.burst_slices)
    assert callable(acars.decode_sliced)
    # The retroactive decoder's private copies are gone: the
    # 0.12.0 conforming implementation moved, it did not fork.
    assert not hasattr(retro, "_am_demod")
    assert not hasattr(retro, "_acars_slices")
    # And every path calls the shared one.
    assert "burst_slices" in inspect.getsource(
        RealSource._feed_acars)
    assert "am_demod" in inspect.getsource(desks.process_probe)
    assert "decode_sliced" in inspect.getsource(
        desks.process_probe)
    assert "am_demod" in inspect.getsource(retro._run_acars)
    assert "decode_sliced" in inspect.getsource(retro._run_acars)


def test_slice_ends_at_the_activity_edge():
    burst = _frame_audio()
    for noise in (0.02, 0.10):
        cap = _capture([(2.0, burst)], noise=noise)
        slices = acars.burst_slices(cap, RATE)
        assert len(slices) == 1
        # The slice covers the whole burst - in heavy noise
        # the smoothed crossing sits inside it, and the guard
        # restores the edge - and overshoots it by a fraction
        # of the old geometry's 1200 margin samples.
        assert len(burst) <= len(slices[0]) <= len(burst) + 240
        assert acars.decode_audio(slices[0], RATE)


# ------------------------------------------------------- regression

def test_live_regression_tuner_margins(profile):
    """One verified frame on a quiet bed, the shape of a real
    tuner capture. Before: the margined slice fed the parity
    gate some fifteen trailing bytes of demodulated noise and
    the frame was discarded. After: the activity-edge slice
    decodes through the desk's live entry point."""
    cap = _capture([(2.0, _frame_audio())])
    assert _old_tuner_messages(cap) == []
    feed, events = _live_feed(profile, cap)
    assert len(feed["messages"]) == 1
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "decode" and ev["grade"] == "CONFIRMED"
    assert ev["emitter"] == "N12345"
    assert ev["data"]["flight"] == "UA12"
    assert "FUEL 9200 KG" in ev["content"]


# ------------------------------------------------------- the matrix
# The 0.12.0 verification matrix, re-run against the live path.

def test_live_matrix_noise_padded_burst(profile):
    # Seconds of quiet padding on both sides of the burst:
    # the padding is capture, not slice.
    feed, events = _live_feed(
        profile, _capture([(3.5, _frame_audio())], seconds=10.0))
    assert len(feed["messages"]) == 1
    assert events[0]["emitter"] == "N12345"


def test_live_matrix_heavier_noise(profile):
    feed, events = _live_feed(
        profile, _capture([(2.0, _frame_audio())], noise=0.10))
    assert len(feed["messages"]) == 1
    assert events[0]["emitter"] == "N12345"


def test_live_matrix_fading_carrier(profile):
    burst = _frame_audio()
    fade = 1.0 - 0.65 * (np.arange(len(burst)) / len(burst))
    feed, events = _live_feed(
        profile, _capture([(2.0, burst * fade)]))
    assert len(feed["messages"]) == 1
    assert events[0]["emitter"] == "N12345"


def test_live_matrix_two_frames_one_window(profile):
    cap = _capture([(1.5, _frame_audio()),
                    (5.0, _frame_audio("POS 39.02 -98.12",
                                        tail="N54321"))])
    # The old geometry lost both frames in the same window.
    assert _old_tuner_messages(cap) == []
    feed, events = _live_feed(profile, cap)
    assert len(feed["messages"]) == 2
    assert {e["emitter"] for e in events} == {"N12345", "N54321"}


def test_live_matrix_noise_alone_files_nothing(profile):
    feed, events = _live_feed(profile, _capture([], noise=0.05))
    assert feed["messages"] == []
    assert events == []


def test_live_silence_files_nothing(profile):
    feed, events = _live_feed(profile, np.zeros(RATE * 8))
    assert feed["messages"] == []
    assert events == []


# ------------------------------------------------------- probe path

def _acars_chan(seed=3):
    """A channel excerpt holding one ACARS burst on a carrier,
    the retained-excerpt shape the retro fixture uses."""
    frame = acars.build_frame_bytes(
        "2", "N12345", " ", "H1", "A",
        "001UA12 OOOI 1234 FUEL 9200 KG")
    audio = acars.bits_to_audio(acars.bytes_to_bits(frame), 48000)
    rng = np.random.default_rng(seed)
    n = 48000 * 2
    chan = (rng.normal(0, 0.02, n)
            + 1j * rng.normal(0, 0.02, n)).astype(complex)
    chan[10000:10000 + len(audio)] += (1.0 + 0.7 * audio)
    return chan


def _excerpt(chan, freq_hz=131.55e6, bw_hz=25000.0):
    data, peak = sigmf.data_bytes(chan)
    return {"b64": base64.b64encode(data).decode("ascii"),
            "rate": 48000.0, "peak": peak, "samples": len(chan),
            "freq_hz": freq_hz, "bw_hz": bw_hz}


def test_probe_attempt_decodes_through_the_front_end():
    chan = _acars_chan()
    # The pre-0.12.1 attempt's route, reproduced: the census
    # demodulator's whole-excerpt audio, global mean and all,
    # fed unsliced to the frame layer at its own rate.
    old_audio = audiocensus._demod(chan, 48000.0, "AM")
    assert acars.decode_audio(old_audio, audiocensus.AUDIO_RATE) \
        == []
    target = {"kind": "frequency", "emitter": None,
              "freq_hz": 131.55e6, "bw_hz": 25000.0,
              "observations": 2, "mean_snr_db": 30.0}
    history = [{"t": T0 - 7200, "family": "fsk-data",
                "snr_db": 20.0},
               {"t": T0 - 3600, "family": "fsk-data",
                "snr_db": 18.0}]
    feed = {"target": target, "target_freq_hz": 131.55e6,
            "snr_db": 15.0, "source": "simulated",
            "census": {"candidates": []},
            "excerpt": _excerpt(chan),
            "context": {"capture_s": 2.5, "pre_s": 10.0,
                        "post_s": 20.0, "note": "test context"}}
    ctx = {"watch_id": "watch-test", "t0": T0, "t1": T0 + 600,
           "profile": None,
           "probe": {"history": history,
                     "coincidence": {"windows_checked": 2,
                                     "by_band": {}, "examples": [],
                                     "window_s": 120.0}}}
    events = desks.process_probe(feed, ctx)
    negatives = [e for e in events
                 if e["data"].get("rule") == "probe_negative_result"]
    assert all(e["data"]["attempted_desk"] != "acars"
               for e in negatives)
    vote = [e for e in events
            if e["data"].get("rule") == "probe_cluster_vote"]
    assert len(vote) == 1
    assert "ACARS decoded in the stare: 1 verified frame(s)" \
        in vote[0]["data"]["positives"]
