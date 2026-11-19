"""0.15.0: the astronomical patch - ephemeris proofs first, then
the desks, the sky account, the Console panel and the products.

The ephemeris fixtures are published values, named at each
assertion with their source and the tolerance the check holds.
Tolerances are set from the method's own stated accuracy class,
not tuned to make the test pass: the JPL approximate elements
carry up to about 400 arcseconds of longitude error for Jupiter
across 1800-2050, Meeus's low-accuracy solar algorithm claims
about 0.01 degrees, and the Meeus central-meridian formulation
is checked against Espenak's published ephemeris at the spread
it actually shows (under 7 degrees across a quarter year).
"""

import datetime
import os

from multidex import ephemeris


def _ep(y, m, d, h=0, mi=0):
    return (datetime.datetime(y, m, d, h, mi,
                              tzinfo=datetime.timezone.utc)
            - datetime.datetime(1970, 1, 1,
                                tzinfo=datetime.timezone.utc)
            ).total_seconds()


def _angdiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


# ------------------------------------------------------- ephemeris

def test_gmst_at_j2000():
    # The IAU expression's own constant: at JD 2451545.0 the
    # GMST is 280.46061837 degrees by definition of the series.
    assert abs(ephemeris.gmst_deg(_ep(2000, 1, 1, 12))
               - 280.46061837) < 0.001


def test_sun_position_against_imcce():
    # IMCCE, "Sun geocentric coordinates" for 2000 Jan 01 00h:
    # apparent RA 18h 42m 54.055s, Dec -23 04' 16.24",
    # distance 0.983331915 AU. Meeus's low-accuracy algorithm
    # is a 0.01-degree-class method; the check holds 0.02.
    pos = ephemeris.sun_position(_ep(2000, 1, 1))
    ra = (18 + 42 / 60 + 54.055 / 3600) * 15.0
    dec = -(23 + 4 / 60 + 16.24 / 3600)
    assert _angdiff(pos["ra_deg"], ra) < 0.02
    assert abs(pos["dec_deg"] - dec) < 0.02
    assert abs(pos["distance_au"] - 0.983331915) < 0.001


def test_jupiter_position_against_published_j2000():
    # In-The-Sky ephemeris (Dominic Ford), J2000 coordinates,
    # 2025 Jan 22 00:00 UTC: RA 04h 39m 02s, Dec +21 35' 55".
    # The JPL approximate elements' stated class for Jupiter
    # is ~400 arcsec worst case; the check holds 0.1 degrees.
    pos = ephemeris.jupiter_position(_ep(2025, 1, 22))
    ra = (4 + 39 / 60 + 2 / 3600) * 15.0
    dec = 21 + 35 / 60 + 55 / 3600
    assert _angdiff(pos["ra_deg"], ra) < 0.1
    assert abs(pos["dec_deg"] - dec) < 0.1


def test_jupiter_distances_against_espenak():
    # Fred Espenak's Geocentric Ephemeris for Jupiter: 2025,
    # 2025 Jan 01 00:00 UTC: distance to Earth 4.19074 AU,
    # to Sun 5.08257 AU. Held to 0.01 AU.
    pos = ephemeris.jupiter_position(_ep(2025, 1, 1))
    assert abs(pos["distance_au"] - 4.19074) < 0.01
    assert abs(pos["sun_distance_au"] - 5.08257) < 0.01


def test_cml3_against_espenak_subearth_longitude():
    # Espenak's 2025 table, the sub-Earth longitude column at
    # 00:00 UTC - the System III central meridian as a full
    # ephemeris computes it. The Meeus formulation this module
    # implements tracks it with a slowly varying offset; the
    # check holds 7 degrees at four points across a quarter.
    for (y, m, d, published) in ((2025, 1, 1, 149.1),
                                 (2025, 2, 1, 135.8),
                                 (2025, 3, 1, 27.7),
                                 (2025, 4, 1, 8.1)):
        got = ephemeris.jupiter_cml3(_ep(y, m, d))
        assert _angdiff(got, published) < 7.0, (y, m, d, got)


def test_io_phase_against_imcce_phenomena():
    # IMCCE, Phenomena of the Galilean satellites 2025 (UT).
    # A transit ingress (TR I) puts Io's centre about one
    # Jovian radius short of the disk centre as seen from
    # Jupiter: phase 180 - 9.7 degrees; an egress (TR E) the
    # same past it; an occultation disappearance (OC D) the
    # same short of superior conjunction, phase 0 - 9.7.
    # Each check holds 5 degrees around that geometry.
    assert _angdiff(ephemeris.io_phase(_ep(2025, 1, 1, 3, 35)),
                    170.3) < 5.0
    assert _angdiff(ephemeris.io_phase(_ep(2025, 1, 8, 7, 32)),
                    189.7) < 5.0
    assert _angdiff(ephemeris.io_phase(_ep(2024, 12, 31, 6, 27)),
                    350.3) < 5.0


def test_radio_jove_1999_storm_lands_in_io_b_box():
    # The Radio JOVE project's own worked example (its CML-Io
    # lesson): a large storm observed 1999 Oct 23 around
    # 2:30 a.m. EDT - 06:30 UTC - with the track passing
    # through the Io-B source. The module's geometry at that
    # instant must favour Io-B.
    geo = ephemeris.jupiter_geometry(_ep(1999, 10, 23, 6, 30))
    assert geo["source"] == "Io-B"


def test_sun_rise_set_solstice():
    # Lat 40, lon 0, 2025 Jun 21: solar noon falls within a
    # couple of minutes of 12:00 UTC (equation of time), the
    # day is about 14h58m long at that latitude on the
    # solstice, so sunrise lands near 04:31 UTC. Held to
    # 10 minutes - the horizon and refraction conventions
    # differ by minutes between almanacs.
    events = dict((kind, t) for t, kind in
                  ephemeris.sun_rise_set(_ep(2025, 6, 21), 40.0, 0.0))
    assert "rise" in events and "set" in events
    sunrise = events["rise"] - _ep(2025, 6, 21)
    assert abs(sunrise - 4.52 * 3600) < 600
    assert abs((events["set"] - events["rise"]) - 14.97 * 3600) < 900


def test_jupiter_windows_respect_altitude_and_boxes():
    # Over any 48 hours the predictor must return only spans
    # whose midpoint geometry is inside the named source's
    # box with Jupiter above the altitude gate - the desk
    # bills nothing the geometry does not support.
    start = _ep(2026, 11, 17, 12)
    wins = ephemeris.jupiter_windows(29.9, -90.0, start,
                                     start + 172800)
    assert wins  # the span searched holds favourable geometry
    for w in wins:
        assert w["end"] > w["start"]
        assert w["mid_alt_deg"] >= 10.0
        assert ephemeris.io_source(w["mid_cml3_deg"],
                                   w["mid_io_phase_deg"]) is not None


def test_next_jupiter_window_is_a_window():
    start = _ep(2026, 11, 17, 12)
    w = ephemeris.next_jupiter_window(29.9, -90.0, start)
    assert w is not None
    assert w["end"] > start


# ---------------------------------------------------------- sonde codec
#
# The in-house sonde codec (multidex.sonde): a complete decoder
# for the frame format the module itself specifies. The proofs
# are roundtrips and rejections - a frame that passes CRC
# decodes to exactly the telemetry encoded, and a frame whose
# CRC fails decodes to nothing, however plausible its bytes.

import numpy as np

from conftest import T0
from multidex import sonde as sonde_mod


def test_sonde_codec_roundtrip_through_noise():
    rate = 48000.0
    p1 = sonde_mod.encode_payload("S12345", 39.5, -98.2, 1200.0,
                                  8.5, 850.0, 40, 3.0)
    p2 = sonde_mod.encode_payload("S12345", 39.51, -98.19, 5400.0,
                                  -12.25, 500.5, 22, 2.9)
    chan = np.concatenate([
        sonde_mod.modulate(p1, rate),
        np.zeros(int(rate * 0.3), dtype=np.complex64),
        sonde_mod.modulate(p2, rate)]).astype(np.complex128)
    rng = np.random.default_rng(7)
    noise = 0.15 * (rng.normal(0, 1, len(chan))
                    + 1j * rng.normal(0, 1, len(chan)))
    frames, _stats = sonde_mod.decode_channel(chan + noise, rate)
    assert len(frames) == 2
    assert frames[0]["serial"] == "S12345"
    assert frames[0]["lat"] == 39.5 and frames[0]["lon"] == -98.2
    assert frames[0]["alt_m"] == 1200.0
    assert frames[0]["temp_c"] == 8.5
    assert frames[0]["pressure_hpa"] == 850.0
    assert frames[1]["alt_m"] == 5400.0
    assert frames[1]["temp_c"] == -12.25


def test_sonde_codec_rejects_corrupt_crc():
    frame = bytearray(sonde_mod.encode_frame(
        sonde_mod.encode_payload("S99999", 30.0, -90.0, 100.0,
                                 20.0, 1000.0, 50, 3.1)))
    frame[-1] ^= 0xFF
    stats = {"sync_found": 0, "crc_failed": 0, "malformed": 0}
    out = sonde_mod._extract_frames(sonde_mod._bytes_to_bits(
        bytes(frame)), stats)
    assert out == []
    assert stats["crc_failed"] == 1


def test_sonde_codec_rejects_implausible_payload():
    # A frame with a valid CRC over a payload whose latitude is
    # outside the world is malformed, not a decode.
    import struct as _struct
    payload = _struct.pack(">B6siiihHBB", 0x01, b"S00001",
                           int(123.0 * 1e7), 0, 0, 0, 0, 0, 0)
    body = bytes([len(payload)]) + payload
    frame = sonde_mod.PREAMBLE + sonde_mod.SYNC \
        + body + sonde_mod.crc16_ccitt(body).to_bytes(2, "big")
    stats = {"sync_found": 0, "crc_failed": 0, "malformed": 0}
    out = sonde_mod._extract_frames(sonde_mod._bytes_to_bits(frame),
                                    stats)
    assert out == []
    assert stats["malformed"] == 1


# --------------------------------------------------------------- desks

from multidex import desks


def _ctx(profile, t0, t1, desk, **extra):
    ctx = {"watch_id": "watch-test", "desk": desk, "t0": t0,
           "t1": t1, "freq_hz": 21.6e6,
           "home": (profile["home"]["lat"], profile["home"]["lon"]),
           "profile": profile, "pass": None}
    ctx.update(extra)
    return ctx


def test_sonde_desk_decodes_channels_and_files_profile(profile):
    from multidex import sigmf
    rate = 48000.0
    parts = []
    for alt in (800.0, 4200.0, 9100.0):
        payload = sonde_mod.encode_payload(
            "S42424", 30.4, -90.4, alt, 15.0 - 0.0065 * alt,
            900.0, 40, 3.0)
        parts.append(sonde_mod.modulate(payload, rate))
        parts.append(np.zeros(int(rate * 0.25),
                              dtype=np.complex64))
    iq = np.concatenate(parts).astype(np.complex128)
    data, peak = sigmf.data_bytes(list(iq))
    import base64 as _b64
    feed = {"channels": [{"freq_hz": 402.0e6,
                          "b64": _b64.b64encode(data).decode(),
                          "rate": rate, "peak": peak,
                          "site": "Test site"}]}
    out = desks.process_sonde(feed, _ctx(profile, T0, T0 + 600,
                                         "sonde"))
    telemetry = [e for e in out if e["kind"] == "telemetry"]
    assert len(telemetry) == 3
    assert all(e["grade"] == "CONFIRMED" for e in telemetry)
    assert telemetry[0]["data"]["decoded"] == "in-house sonde codec"
    profiles = [e for e in out if e["kind"] == "pass"
                and (e["data"] or {}).get("profile")]
    assert len(profiles) == 1
    assert profiles[0]["data"]["max_alt_m"] == 9100.0
    assert profiles[0]["data"]["frames"] == 3


def test_sonde_desk_carrier_wording_states_the_attempt(profile):
    feed = {"carriers": [{"freq_hz": 402.2e6, "peak_dbm": -80.0,
                          "noise_floor_db": -105.0}],
            "channels": []}
    out = desks.process_sonde(feed, _ctx(profile, T0, T0 + 600,
                                         "sonde"))
    assert len(out) == 1
    assert "no frame from this carrier passed" in out[0]["content"]
    assert out[0]["data"]["decode_attempted"] is True
    feed2 = {"carriers": [{"freq_hz": 402.2e6, "peak_dbm": -80.0,
                           "noise_floor_db": -105.0}]}
    out2 = desks.process_sonde(feed2, _ctx(profile, T0, T0 + 600,
                                           "sonde"))
    assert "no channel capture was available" in out2[0]["content"]
    assert out2[0]["data"]["decode_attempted"] is False


def _excerpt_of(iq, rate):
    from multidex import audiocensus
    return audiocensus._excerpt(np.asarray(iq, dtype=np.complex128),
                                rate)


def test_jupiter_desk_reads_l_burst_and_holds_the_line(profile):
    fs = 48000.0
    n = int(fs * 2.5)
    gen = np.random.default_rng(3)
    t = np.arange(n) / fs
    raw = gen.normal(0, 1, n) + 1j * gen.normal(0, 1, n)
    spec = np.fft.fft(raw)
    fr = np.fft.fftfreq(n, 1.0 / fs)
    burst = np.fft.ifft(np.where(np.abs(fr) <= 20e3, spec, 0.0))
    burst = burst / (np.max(np.abs(burst)) + 1e-9)
    gate = np.clip(np.sin(np.pi * t / 2.5) ** 2 * 1.4, 0.0, 1.0)
    iq = (gen.normal(0, 0.02, n) + 1j * gen.normal(0, 0.02, n)) \
        + 0.85 * gate * burst
    feed = {"excerpt": _excerpt_of(iq, fs), "span_note": "test span"}
    ctx = _ctx(profile, T0, T0 + 900, "jupiter",
               jupiter_window={"source": "Io-C",
                               "mid_cml3_deg": 320.0,
                               "mid_io_phase_deg": 240.0,
                               "mid_alt_deg": 42.0})
    out = desks.process_jupiter(feed, ctx)
    assert len(out) == 1
    ev = out[0]
    assert ev["kind"] == "observation" and ev["emitter"] == "JUPITER"
    assert ev["grade"] == "PROBABLE"  # a hearing never confirms
    assert ev["data"]["morphology"]["class"].startswith("L-burst-like")
    assert "not a confirmation" in ev["content"]
    assert ev["data"]["geometry"]["cml3_deg"] == 320.0


def test_jupiter_desk_calls_a_narrowband_carrier_terrestrial(profile):
    fs = 48000.0
    n = int(fs * 2.5)
    gen = np.random.default_rng(5)
    t = np.arange(n) / fs
    iq = (gen.normal(0, 0.02, n) + 1j * gen.normal(0, 0.02, n)) \
        + 0.4 * np.exp(2j * np.pi * 10e3 * t)
    feed = {"excerpt": _excerpt_of(iq, fs), "span_note": "test span"}
    ctx = _ctx(profile, T0, T0 + 900, "jupiter",
               jupiter_window={"source": "Io-B",
                               "mid_cml3_deg": 150.0,
                               "mid_io_phase_deg": 95.0,
                               "mid_alt_deg": 55.0})
    out = desks.process_jupiter(feed, ctx)
    assert out[0]["data"]["morphology"]["class"] == \
        "narrowband carrier"
    assert "terrestrial character" in out[0]["content"]


def test_jupiter_desk_files_a_quiet_window_as_a_result(profile):
    fs = 48000.0
    n = int(fs * 2.5)
    gen = np.random.default_rng(11)
    iq = gen.normal(0, 0.02, n) + 1j * gen.normal(0, 0.02, n)
    feed = {"excerpt": _excerpt_of(iq, fs), "span_note": "test span"}
    ctx = _ctx(profile, T0, T0 + 900, "jupiter",
               jupiter_window={"source": "non-Io-A",
                               "mid_cml3_deg": 250.0,
                               "mid_io_phase_deg": 10.0,
                               "mid_alt_deg": 30.0})
    out = desks.process_jupiter(feed, ctx)
    assert out[0]["kind"] == "census"
    assert out[0]["data"]["heard"] is False
    assert "a quiet window" in out[0]["content"]


def test_solar_desk_reads_a_collapse_from_its_own_history(profile):
    desks.reset_state()
    # 19:00 UTC at the reference home (lon -98): daylight.
    t = T0 + 7 * 3600
    strong = {"noise_floor_db": -98.0, "emissions": [
        {"freq_hz": 7.41e6, "bw_hz": 10e3, "peak_dbm": -70.0,
         "shape": "am-broadcast"},
        {"freq_hz": 13.845e6, "bw_hz": 10e3, "peak_dbm": -72.0,
         "shape": "am-broadcast"}]}
    ctx = _ctx(profile, t, t + 300, "solar")
    out1 = desks.process_solar(strong, ctx)
    assert not any((e["data"] or {}).get("disturbance")
                   for e in out1)
    faded = {"noise_floor_db": -99.0, "emissions": [
        {"freq_hz": 7.41e6, "bw_hz": 10e3, "peak_dbm": -97.0,
         "shape": "am-broadcast"},
        {"freq_hz": 13.845e6, "bw_hz": 10e3, "peak_dbm": -98.0,
         "shape": "am-broadcast"}]}
    ctx2 = _ctx(profile, t + 600, t + 900, "solar")
    out2 = desks.process_solar(faded, ctx2)
    dist = [e for e in out2 if (e["data"] or {}).get("disturbance")]
    assert len(dist) == 1
    assert dist[0]["data"]["flash"] is True
    assert dist[0]["data"]["flash_class"] == \
        "multi-band ionospheric collapse"
    assert "not the Sun directly" in dist[0]["content"]
    # The latch holds: a third faded feed files no second event.
    out3 = desks.process_solar(faded, _ctx(profile, t + 1200,
                                           t + 1500, "solar"))
    assert not any((e["data"] or {}).get("disturbance")
                   for e in out3)
    desks.reset_state()


def test_solar_desk_files_broadband_burst_in_daylight(profile):
    desks.reset_state()
    t = T0 + 7 * 3600
    feed = {"noise_floor_db": -100.0, "emissions": [
        {"freq_hz": 21.0e6, "bw_hz": 2.0e6, "peak_dbm": -82.0,
         "shape": "solar-burst"}]}
    out = desks.process_solar(feed, _ctx(profile, t, t + 300,
                                         "solar"))
    bursts = [e for e in out if (e["data"] or {}).get("burst")]
    assert len(bursts) == 1
    assert "the station's own observation" in bursts[0]["content"]
    desks.reset_state()


def test_meteor_desk_counts_pings_and_states_the_span(profile):
    feed = {"pings": [
        {"freq_hz": 50.31e6, "offset_s": 12.0, "dur_s": 0.4,
         "snr_db": 15.0, "peak_dbm": -95.0},
        {"freq_hz": 50.31e6, "offset_s": 200.0, "dur_s": 0.2,
         "snr_db": 9.0, "peak_dbm": -101.0}],
        "sampled_s": 300.0}
    out = desks.process_meteor(feed, _ctx(profile, T0, T0 + 300,
                                          "meteor"))
    pings = [e for e in out if (e["data"] or {}).get("ping")]
    assert len(pings) == 2
    assert "not a station identified" in pings[0]["content"]
    census = [e for e in out if e["kind"] == "census"]
    assert census[0]["data"]["pings"] == 2
    assert census[0]["data"]["sampled_s"] == 300.0


def test_hydrogen_stands_benched_on_its_hardware_gate(profile):
    from multidex.desks import desk_state
    for kind in ("simulated", "real"):
        state, reason = desk_state("hydrogen", profile, kind, None)
        assert state == "benched"
        assert "dish" in reason and "low-noise amplifier" in reason
    assert desks.process_hydrogen({}, _ctx(profile, T0, T0 + 60,
                                           "hydrogen")) == []


def test_new_desks_judged_in_applicability(profile):
    from multidex import applicability
    verdicts = applicability.evaluate(profile)
    for desk in ("jupiter", "solar", "meteor", "hydrogen"):
        assert verdicts[desk]["stands"], desk
        # The judgment itself is stated in the RULES table;
        # a standing desk's verdict reason is None by design.
        assert applicability.RULES[desk][1], desk


def test_officer_bills_jupiter_windows_as_class_a(profile):
    from multidex import officer
    profile["home"]["lat"] = 29.9
    profile["home"]["lon"] = -90.0
    items = officer.jupiter_items(profile, T0, T0 + 7200)
    assert items, "the coastal test home has an Io-C window at T0"
    for item in items:
        assert item["cls"] == "A" and item["kind"] == "jupiter"
        assert item["desks"] == ["jupiter"]
        assert item["jupiter_window"]["source"]
    bill = officer.build_bill(profile, T0, [])
    assert any(b["kind"] == "jupiter" for b in bill)
    assert "meteor" in officer.ROTATION


# ------------------------------------------------------ feed and panel

def test_display_feed_carries_sky_records(tmp_path):
    from multidex import displayfeed
    feed = displayfeed.DisplayFeed(str(tmp_path))
    feed.sky({"t": T0, "lst_deg": 123.4,
              "jupiter": {"alt_deg": 40.0, "source_now": "Io-C",
                          "next_window_start": T0 + 3600,
                          "next_source": "Io-B"},
              "solar": {"sun_alt_deg": 20.0, "daylight": True,
                        "last_state": None},
              "meteor": {"pings": 3, "rate_per_sampled_hour": 12.0},
              "sonde": {"last_serial": None,
                        "last_max_alt_m": None}})
    loaded = displayfeed.load_feed(str(tmp_path))
    assert len(loaded["sky"]) == 1
    assert loaded["sky"][0]["jupiter"]["next_source"] == "Io-B"


def test_console_sky_panel_renders_records_and_holds_honestly():
    from multidex import console
    empty = console._sky_rows({"sky": []}, 76, 6)
    flat = []
    for row in empty:
        for seg in row:
            flat.append(seg[0] if isinstance(seg, tuple) else seg)
    assert any("No sky account" in line for line in flat)
    feed = {"sky": [{"t": T0, "lst_deg": 200.0,
                     "jupiter": {"alt_deg": 44.0,
                                 "source_now": "Io-C",
                                 "next_window_start": T0 + 5400,
                                 "next_source": "Io-B"},
                     "solar": {"sun_alt_deg": 30.0,
                               "daylight": True,
                               "last_state": "band state: 20 m"},
                     "meteor": {"pings": 5,
                                "rate_per_sampled_hour": 15.0},
                     "sonde": {"last_serial": "S42424",
                               "last_max_alt_m": 9100.0}}]}
    rows = console._sky_rows(feed, 76, 10)
    flat = []
    for row in rows:
        for seg in row:
            flat.append(seg[0] if isinstance(seg, tuple) else seg)
    joined = " ".join(flat)
    assert "Jupiter" in joined and "Io-B" in joined
    assert "meteors: 5 ping(s)" in joined
    assert "S42424" in joined


def test_sky_fold_bins_by_sidereal_hour(index):
    from multidex import sky as sky_mod
    from multidex.events import make_event
    home = {"lat": 39.0, "lon": -98.0, "alt_m": 300}
    for k in range(6):
        ev = make_event("watch-test", "sweep", "observation",
                        T0 + k * 3600, T0 + k * 3600 + 300,
                        freq_hz=7.0e6, grade="PROBABLE",
                        data={"noise_floor_db": -100.0 - k})
        index.add_event(ev)
    account = sky_mod.build_account(index, home)
    assert account["events_folded"] == 6
    assert sum(b["count"] for b in account["bins"]) == 6
    lines = sky_mod.render_fold_lines(account)
    assert any("reading" in line for line in lines)
    mh = sky_mod.meteor_hours(index, "watch-test", home)
    assert mh["total_pings"] == 0


# ------------------------------------------------------------ end to end

def test_simulated_watch_files_the_sky_account(home, profile):
    import os
    from multidex.index import Index
    from multidex.officer import WatchRunner
    # Synthetic coastal home with a synthetic nearby sonde
    # site, all test positions invented for the test.
    profile["home"]["lat"] = 29.9
    profile["home"]["lon"] = -90.0
    profile["sondes"]["sites"] = [
        {"name": "Test site", "lat": 30.4, "lon": -90.4}]
    profile["rotation"]["meteor"] = {"weight": 40, "dwell_s": 300}
    profile["watch"]["hours"] = 2
    profile["watch"]["sitrep_hours"] = 1
    profile["advanced"]["time_scale"] = 100000.0
    index = Index(home)
    try:
        runner = WatchRunner(home, profile, index)
        result = runner.start(start_epoch=T0)
        assert result["ok"], result
        watch_id = result["watch_id"]
    finally:
        index.close()
    wdir = os.path.join(home, "watches", watch_id)
    assert os.path.exists(os.path.join(wdir, "sky.json"))
    report = open(os.path.join(wdir, "REPORT.md")).read()
    assert "## 8. Astronomy and atmosphere account" in report
    assert "sidereal" in report
    index = Index(home)
    try:
        desks_seen = index.counts_by_desk(watch_id)
        # The Jupiter desk heard the world's planted storm in
        # its predicted window, or filed the window quiet -
        # either way it stood and reported.
        assert desks_seen.get("jupiter", 0) > 0
        hearings = [e for e in index.events(
            watch_id=watch_id, desk="jupiter",
            kinds=["observation"], limit=100)]
        assert hearings, "the planted storm stands in a window " \
                         "the bill serves inside two hours"
        assert all(e["grade"] == "PROBABLE" for e in hearings)
        assert desks_seen.get("solar", 0) > 0
        assert desks_seen.get("meteor", 0) > 0
        pings = [e for e in index.events(
            watch_id=watch_id, desk="meteor",
            kinds=["observation"], limit=1000)
            if (e["data"] or {}).get("ping")]
        assert pings, "pre-dawn at the test home: pings expected"
        telemetry = [e for e in index.events(
            watch_id=watch_id, desk="sonde",
            kinds=["telemetry"], limit=100)]
        assert telemetry, "the sonde ascent decodes in-house"
        assert all((e["data"] or {}).get("decoded")
                   == "in-house sonde codec" for e in telemetry)
        from multidex import products
        assert "Sky account" in products.ask(index, "sky")
    finally:
        index.close()
