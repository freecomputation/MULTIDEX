"""0.16.0: the spectrum, live - streaming spectra and a dense
render for the Console.

The operator's ruling, from watching the Console on his own
machine: stream it live and render it dense. The acceptance
discipline is the house one. The simulated source's stream is
asserted frame by frame - cadence, timestamps inside the
dwell span, the world at each frame's instant. The census
engine's block stream is asserted against the slice that
produced it: a burst lands in its own block's frame, and the
analysis result is unchanged by streaming. The real source's
streaming points are exercised against a stubbed capture.
Two simulated watches prove the extended invariance law: feed
on with the stream running, feed off, identical records. The
dense render is asserted structurally - a peaked spectrum
fills its columns, it does not scatter marks - and a held
panel names the tuner's actual work.
"""

import copy
import math
import os

import numpy as np

from multidex import __version__, audiocensus, console, dashboard
from multidex import displayfeed, tuner
from multidex.index import Index
from multidex.officer import WatchRunner
from multidex.sim import SimSource

from conftest import T0

START = 1000000.0
WID = "watch-0160-test"


def test_version():
    assert __version__ == "0.16.0"


# --------------------------------------------------- sim streaming

def test_sim_capture_streams_at_the_cadence(profile):
    source = SimSource(profile, T0)
    frames = []
    source.capture(162.4e6, 2.4e6, T0, T0 + 600,
                   [{"desk": "sweep", "freq_hz": 162.4e6,
                     "bw_hz": 2.4e6}], stream=frames.append)
    # A frame every STREAM_CADENCE_S of dwell time, strictly
    # inside the dwell's span, in order.
    assert len(frames) >= 100
    times = [f["t"] for f in frames]
    assert all(T0 < t < T0 + 600 for t in times)
    assert times == sorted(times)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(abs(g - displayfeed.STREAM_CADENCE_S) < 1e-6
               for g in gaps)
    for frame in frames:
        assert frame["hi_hz"] > frame["lo_hz"]
        assert len(frame["bins"]) == 120
        assert frame["brackets"] == []


def test_sim_stream_frames_are_the_world_at_their_instant(profile):
    source = SimSource(profile, T0)
    frames = []
    source.capture(162.4e6, 2.4e6, T0, T0 + 60,
                   [{"desk": "sweep", "freq_hz": 162.4e6,
                     "bw_hz": 2.4e6}], stream=frames.append)
    assert frames
    for frame in frames:
        again = source._display_at(162.4e6, 2.4e6, frame["t"])
        assert again["bins"] == frame["bins"]
        assert again["floor_db"] == frame["floor_db"]


def test_sim_scan_output_is_unchanged_by_the_extraction(profile):
    # The cadence renderer shares scan's model and rendering;
    # scan's own frame must be what it was before the split.
    source = SimSource(profile, T0)
    out = source.scan(162.4e6, 2.4e6, T0, T0 + 60)
    disp = out["display"]
    assert disp["lo_hz"] == 162.4e6 - 1.2e6
    assert disp["hi_hz"] == 162.4e6 + 1.2e6
    peak_i = int(np.argmax(disp["bins"]))
    peak_f = disp["lo_hz"] + (peak_i + 0.5) / len(disp["bins"]) \
        * (disp["hi_hz"] - disp["lo_hz"])
    assert abs(peak_f - 162.475e6) < 100e3


# ------------------------------------------------ census streaming

def _slice_with_a_late_burst():
    fs = 240000.0
    n = int(fs * 2.5)
    rng = np.random.default_rng(11)
    iq = (0.03 * (rng.standard_normal(n)
                  + 1j * rng.standard_normal(n))).astype(np.complex128)
    t = np.arange(n) / fs
    iq[int(fs * 2.0):] += 0.6 * np.exp(
        2j * np.pi * 30000.0 * t[int(fs * 2.0):])
    return iq, fs


def test_analyze_streams_block_frames_and_changes_nothing():
    iq, fs = _slice_with_a_late_burst()
    frames = []
    out = audiocensus.analyze(iq, fs, 100e6, stream=frames.append,
                              t_start=T0)
    plain = audiocensus.analyze(iq, fs, 100e6)
    # The analysis is the analysis, stream or no stream.
    assert out["display"] == plain["display"]
    assert out["noise_floor_db"] == plain["noise_floor_db"]
    assert [(c["freq_hz"], c["grade"], c["modulation"])
            for c in out["candidates"]] == \
        [(c["freq_hz"], c["grade"], c["modulation"])
         for c in plain["candidates"]]
    # One frame per half-second block of the slice, timestamped
    # inside the slice's span, in order, brackets empty - no
    # desk has filed yet when a stream frame is measured.
    assert len(frames) == 5
    times = [f["t"] for f in frames]
    assert times == sorted(times)
    assert all(T0 <= t <= T0 + 2.5 for t in times)
    for frame in frames:
        assert frame["brackets"] == []
        assert len(frame["bins"]) <= displayfeed.MAX_BINS
    # The burst lives in the last block: its frame stands well
    # over the first block's quiet one.
    assert max(frames[-1]["bins"]) > max(frames[0]["bins"]) + 15.0


def test_analyze_without_a_stream_publishes_nothing():
    iq, fs = _slice_with_a_late_burst()
    frames = []
    audiocensus.analyze(iq, fs, 100e6, stream=None, t_start=T0)
    audiocensus.analyze(iq, fs, 100e6, stream=frames.append,
                        t_start=None)
    assert frames == []


def test_a_broken_stream_never_reaches_the_census():
    iq, fs = _slice_with_a_late_burst()

    def broken(_frame):
        raise RuntimeError("the glass is down")

    out = audiocensus.analyze(iq, fs, 100e6, stream=broken,
                              t_start=T0)
    plain = audiocensus.analyze(iq, fs, 100e6)
    assert out["display"] == plain["display"]
    assert len(out["candidates"]) == len(plain["candidates"])


# -------------------------------------------------- real streaming

def _real_source(monkeypatch, profile, raw):
    source = tuner.RealSource(profile)
    monkeypatch.setattr(source, "_iq", lambda *a, **k: raw)
    return source


def test_real_scan_streams_its_frame_once(profile, monkeypatch):
    rng = np.random.default_rng(3)
    raw = rng.integers(0, 256, 262144 * 2,
                       dtype=np.uint8).tobytes()
    source = _real_source(monkeypatch, profile, raw)
    frames = []
    source._stream = frames.append
    out = source.scan(120e6, 2.4e6, T0, T0 + 120)
    assert len(frames) == 1
    frame = frames[0]
    assert frame["lo_hz"] == out["display"]["lo_hz"]
    assert frame["hi_hz"] == out["display"]["hi_hz"]
    assert frame["bins"] == out["display"]["bins"]
    assert frame["brackets"] == []
    assert isinstance(frame["t"], float) and frame["t"] > 0
    # A cached scan is the same measurement: it does not
    # publish a second frame claiming a new one.
    source.scan(120e6, 2.4e6, T0, T0 + 120)
    assert len(frames) == 1


def test_real_census_slice_streams_block_frames(profile,
                                                monkeypatch):
    # One second of noise at the census rate stands in for the
    # five-second slice: the feed reads what the capture holds,
    # and the stream frames its half-second blocks.
    rng = np.random.default_rng(5)
    raw = rng.integers(0, 256, 2400000 * 2,
                       dtype=np.uint8).tobytes()
    source = _real_source(monkeypatch, profile, raw)
    frames = []
    feeds = source.capture(
        120e6, 2.4e6, T0, T0 + 300,
        [{"desk": "audiocensus", "freq_hz": 120e6,
          "bw_hz": 2.4e6}], stream=frames.append)
    assert "audiocensus" in feeds
    assert len(frames) >= 2
    times = [f["t"] for f in frames]
    assert times == sorted(times)
    for frame in frames:
        assert frame["brackets"] == []
        assert len(frame["bins"]) <= displayfeed.MAX_BINS
    # The stream belongs to the capture: it is cleared when
    # the capture ends.
    assert source._stream is None


# ------------------------------------------------------------ rings

def test_streaming_ring_holds_only_the_recent_frames(tmp_path):
    feed = displayfeed.DisplayFeed(str(tmp_path))
    for i in range(200):
        feed.spectrum({"t": float(i), "lo_hz": 1e6, "hi_hz": 2e6,
                       "floor_db": -100.0, "bins": [-100.0] * 10,
                       "brackets": []})
    loaded = displayfeed.load_feed(str(tmp_path))
    times = [f["t"] for f in loaded["spectra"]]
    assert len(times) == displayfeed.MAX_SPECTRA
    assert times == [float(i) for i in range(160, 200)]


# ------------------------------------------------- watch invariance

def _run_watch(home, profile, hours=1):
    profile["watch"]["hours"] = hours
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    try:
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=T0)
        assert result["ok"], result
        return result["watch_id"]
    finally:
        index.close()


def _event_record(home, watch_id):
    index = Index(home)
    try:
        evs = index.events(watch_id=watch_id, limit=1000000)
        return [(ev["desk"], ev["kind"], ev["grade"],
                 ev["content"], ev["freq_hz"]) for ev in evs]
    finally:
        index.close()


def test_watch_streams_and_the_record_is_identical(tmp_path,
                                                   profile):
    home_on = str(tmp_path / "on")
    home_off = str(tmp_path / "off")
    prof_on = copy.deepcopy(profile)
    prof_off = copy.deepcopy(profile)
    prof_off["advanced"]["display_feed"] = False
    wid_on = _run_watch(home_on, prof_on)
    wid_off = _run_watch(home_off, prof_off)
    # The extended invariance law: streaming changes nothing
    # in the record.
    assert _event_record(home_on, wid_on) == \
        _event_record(home_off, wid_off)
    dir_off = os.path.join(home_off, "watches", wid_off)
    assert not os.path.exists(
        os.path.join(dir_off, displayfeed.SPECTRA_NAME))
    # The streaming watch published far more frames than it
    # ran dwells: the ring stands full at forty, and the tail
    # carries many frames from a single dwell.
    feed = displayfeed.load_feed(
        os.path.join(home_on, "watches", wid_on))
    spectra = feed["spectra"]
    assert len(spectra) == displayfeed.MAX_SPECTRA
    index = Index(home_on)
    try:
        dwells = [ev for ev in index.events(watch_id=wid_on,
                                            limit=1000000)
                  if ev["kind"] == "dwell"]
    finally:
        index.close()
    assert len(dwells) < displayfeed.MAX_SPECTRA
    by_bill = {}
    for frame in spectra:
        by_bill[frame["bill_id"]] = by_bill.get(
            frame["bill_id"], 0) + 1
    assert max(by_bill.values()) >= 5
    times = [f["t"] for f in spectra]
    assert times == sorted(times)
    assert all(T0 <= t <= T0 + 3600 for t in times)


# ------------------------------------------------------ dense render

def _raw(elapsed=3600.0):
    bill = [
        {"id": "bill-001", "cls": "C", "kind": "sweep",
         "label": "Census sweep", "start": START + 3000,
         "dur": 600, "center_hz": 14.2e6, "bw_hz": 2.4e6,
         "desks": ["sweep"], "pass": None},
    ]
    meta = {"start": START, "planned_end": START + 86400,
            "source": "simulated", "time_scale": 1.0,
            "region": "test region", "profile_name": "default"}
    state = {"next_index": 1, "elapsed_s": elapsed,
             "current": "Census sweep (sweep)", "benched": [],
             "complete": False, "pid": os.getpid(),
             "executing": "bill-001",
             "budget": {"cap_bytes": 20000000000,
                        "consumed_bytes": 3000000000,
                        "reserved_bytes": 6000000000,
                        "free_bytes": 10000000000,
                        "watch_granted_bytes": 5000000000,
                        "watch_consumed_bytes": 100000000}}
    return {"watch_id": WID, "state": state,
            "state_mtime": START + elapsed,
            "meta": meta, "bill": bill, "feed_events": [],
            "band_counts": {}, "executed_s": 3000.0, "gaps": 0,
            "events_total": 42, "dossiers_total": 3,
            "snippets": {"kept_count": 1, "kept_bytes": 100,
                         "shed_count": 0, "shed_bytes": 0},
            "bench_reasons": {}, "flash_ids": set(),
            "flash_count": 0, "pool": state["budget"],
            "update": None}


def _view(elapsed=3600.0):
    # The view's clock: the state was saved at START + elapsed
    # with that elapsed, and now is that same moment, so the
    # watch clock stands at START + elapsed.
    return dashboard.build_view(_raw(elapsed),
                                now=START + elapsed)


def _peaked_frame(t, peak_db=44.0):
    bins = []
    for i in range(240):
        d = (i - 120) / 20.0
        bins.append(-110.0 + peak_db * math.exp(-0.5 * d * d))
    return {"t": t, "bill_id": "bill-001", "label": "Census sweep",
            "kind": "sweep", "lo_hz": 13.1e6, "hi_hz": 15.3e6,
            "floor_db": -110.0, "bins": bins, "brackets": []}


def _waterfall_text(view, feed, width=210, height=58):
    frame = console.build_frame(view, feed, width, height)
    text = console.frame_text(frame)
    title_i = next(i for i, row in enumerate(text)
                   if row.startswith("SPECTRUM"))
    axis_i = next(i for i in range(title_i + 1, len(text))
                  if "13.100 MHz" in text[i]
                  and "15.300 MHz" in text[i])
    return text, title_i, axis_i


def test_dense_plot_fills_the_peak_columns():
    frame = _peaked_frame(START + 3590.0, 46.0)
    feed = {"spectra": [frame], "work": [], "thought": []}
    text, title_i, axis_i = _waterfall_text(_view(), feed)
    plot = text[title_i + 1:axis_i]
    # The newest frame stands as a multi-row filled plot.
    assert len(plot) >= 4
    width = 210
    filled = [sum(1 for row in plot if row[x] != " ")
              for x in range(width)]
    peak_col = max(range(width), key=lambda x: filled[x])
    # At the peak the column is filled in every plot row, its
    # foot a full block - a filled curve, not a row of
    # isolated marks.
    assert filled[peak_col] == len(plot)
    assert plot[-1][peak_col] == "█"
    assert plot[0][peak_col] != " "
    # The plot's area is filled, not traced: its glyphs
    # outnumber the marks a one-row rendering of the same
    # frame would draw (columns past the old ramp's first
    # step) by a wide factor.
    pooled = displayfeed.pool_bins(frame["bins"], width)
    marks = sum(1 for v in pooled
                if (v - frame["floor_db"]) / 48.0 >= 1.0 / 9.0)
    glyphs = sum(1 for row in plot for ch in row if ch != " ")
    assert glyphs > 2 * marks
    # Off the peak's shoulders the fill falls away honestly.
    assert filled[0] < filled[peak_col]
    assert filled[width - 1] < filled[peak_col]


def test_history_rows_are_braille_columns():
    frames = [_peaked_frame(START + 3000.0, 30.0),
              _peaked_frame(START + 3300.0, 36.0),
              _peaked_frame(START + 3590.0, 44.0)]
    feed = {"spectra": frames, "work": [], "thought": []}
    text, title_i, axis_i = _waterfall_text(_view(), feed)
    body = text[title_i + 1:axis_i]
    assert any(any(ch in "⣀⣤⣶⣿" for ch in row) for row in body)


def test_held_panel_names_the_tuners_work():
    # Mid-dwell (the sweep item runs START+3000 to START+3600)
    # with the newest frame long past: the panel holds, and
    # says what the tuner is on while it holds.
    frame = _peaked_frame(START + 60.0)
    feed = {"spectra": [frame], "work": [], "thought": []}
    text, title_i, _axis_i = _waterfall_text(_view(3300.0), feed)
    title = text[title_i]
    assert "HELD" in title
    assert "ago" in title
    assert "tuner on Census sweep" in title


def test_fresh_streaming_frame_does_not_read_held():
    frame = _peaked_frame(START + 3295.0)
    feed = {"spectra": [frame], "work": [], "thought": []}
    text, title_i, _axis_i = _waterfall_text(_view(3300.0), feed)
    title = text[title_i]
    assert "HELD" not in title
    assert "last capture" in title
