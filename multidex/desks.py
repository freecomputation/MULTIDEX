"""The desks: decoder workers behind the pipe contract (design 5, 7).

Each standing desk is a pure function process(feed, ctx) -> [events].
The supervisor runs these functions in persistent subprocesses
(multidex.deskworker); tests may call them directly. A desk emits
normalized events and nothing else. It cannot retune the radio and
cannot write outside its own output: snippet bytes travel back inside
an event's data under "_snippet" and the Watch Officer files them.

Tiers are reported exactly as the design assigns them. Desks whose
decode needs an external program this pure-Python build does not bind
are benched with the reason stated, never silently absent.
"""

import base64
import math
import random
import struct

from . import codecs, modes
from .bands import band_name
from .events import make_event

# VHF airband, AM: the verified local set for the deployment region
# (approach sectors, tower, clearance, operations and guard for the
# Army airfield whose traffic dominates this airspace). Verified
# against the FAA airport record and the regional ATC frequency
# listings; see the 0.2.0 research notes. The 0.1.x list held none
# of the sector frequencies and is retired.
AIRBAND_CHANNELS = [118.075e6, 121.100e6, 121.500e6, 125.400e6,
                    126.200e6, 133.450e6, 133.750e6, 135.200e6]

# UHF military airband, AM: the same airfield's UHF pairs, including
# the north-sector pair and the UHF guard frequency.
MILAIR_CHANNELS = [239.275e6, 243.000e6, 248.550e6, 270.350e6,
                   319.250e6, 327.125e6, 336.300e6, 371.350e6]

# name -> (tier, service, cpu class, channels [(freq, bw)] or "window")
SPECS = {
    "adsb":      (1, "ADS-B aircraft", "light",
                  [(1090e6, 2e6)]),
    "airband":   (1, "Airband voice", "moderate",
                  [(f, 25e3) for f in AIRBAND_CHANNELS]),
    "milair":    (1, "Military UHF airband voice", "moderate",
                  [(f, 25e3) for f in MILAIR_CHANNELS]),
    "audiocensus": (3, "Audio emissions census", "moderate", "window"),
    "acars":     (1, "ACARS datalink", "light",
                  [(131.45e6, 25e3), (131.55e6, 25e3), (131.725e6, 25e3)]),
    "ais":       (1, "AIS vessels", "light",
                  [(161.975e6, 25e3), (162.025e6, 25e3)]),
    "wxradio":   (1, "Weather radio and SAME", "light",
                  [(162.475e6, 25e3)]),
    "ism":       (1, "ISM devices", "light", "window"),
    "aprs":      (1, "APRS packet", "light", [(144.39e6, 12.5e3)]),
    "pager":     (1, "POCSAG paging", "light", [(929.6125e6, 25e3)]),
    "satellite": (2, "Weather satellite passes", "heavy",
                  [(137.1e6, 150e3)]),
    "sonde":     (2, "Radiosondes", "light", "window"),
    "broadcast": (2, "Broadcast census", "light", "window"),
    "vdl2":      (2, "VDL2 datalink", "light", [(136.975e6, 25e3)]),
    "amateur":   (2, "Amateur SSB/CW/digital", "moderate", "window"),
    "digitalvoice": (2, "DMR/P25/NXDN voice", "heavy", "window"),
    "hfdl":      (2, "HF datalink", "light", "window"),
    "iss":       (2, "ISS voice and SSTV", "light", [(145.8e6, 25e3)]),
    "cellular":  (3, "Cellular survey", "light", "window"),
    "trunking":  (3, "Trunking census", "light", "window"),
    "sweep":     (3, "Spectrum census and anomaly", "light", "window"),
}

# Desks this build benches with the reason stated, whatever the
# profile says. In 0.2.0 only HFDL remains here; the desks that
# were benched in 0.1.x stand now, in person or through a bound
# host tool (see CONDITIONAL_BENCHED and toolbind).
ALWAYS_BENCHED = {
    "hfdl": "HFDL decode stays benched in this build: the decoder "
            "(dumphfdl) needs a SoapySDR, liquid-dsp, FFTW and "
            "libacars chain with no user-space staging route on "
            "this host; its channels span 3-22 MHz and need a "
            "propagation-driven band rotation this build does not "
            "carry; and the fixed antenna is a VHF/UHF antenna. HF "
            "spectrum is censused by the sweep and audio census "
            "desks on every HF dwell, and no HFDL decode is claimed",
}

# Desks that stand on the real source when a host decoder program
# is present and bench with the staging instruction when it is
# not. On the simulated source they stand (the world feeds them).
CONDITIONAL_BENCHED = {}  # filled from toolbind below, by desk_state

# Kept for callers that still import it: in 0.2.0 no desk benches
# on the real source merely for being real - ACARS decodes in pure
# Python from the audio path.
REAL_BENCHED = {}

TRANSCRIPTION_REASON = ("no transcription engine is bound on this "
                        "host (the faster-whisper stack is not "
                        "importable here); voice audio is filed as "
                        "snippets and transcripts are reported as "
                        "not produced")


def transcription_state():
    """('bound', None) when a transcription engine is importable on
    this host, else ('benched', reason). The engine is the host's
    faster-whisper stack; the queue that feeds it is bounded and
    triage-gated (multidex.transcribe)."""
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is not None:
        return "bound", None
    return "benched", TRANSCRIPTION_REASON


def channels_for(name):
    spec = SPECS[name]
    if spec[3] == "window":
        return [(None, None)]
    return list(spec[3])


def desk_state(name, profile, source_kind, capabilities):
    """('standing', None) or ('benched', plain-word reason)."""
    if name in ALWAYS_BENCHED:
        return "benched", ALWAYS_BENCHED[name]
    if source_kind == "real":
        from .toolbind import TOOL_DESKS, tool_available
        if name in TOOL_DESKS and not tool_available(TOOL_DESKS[name][0]):
            return "benched", TOOL_DESKS[name][1]
    if source_kind == "real" and name in REAL_BENCHED:
        return "benched", REAL_BENCHED[name]
    if name in profile["desks"]["bench"]:
        return "benched", "benched by the watch profile"
    if name not in profile["desks"]["stand"]:
        return "benched", "not in the profile desk roster"
    if capabilities is not None and name not in capabilities:
        return "benched", ("the %s source cannot feed this desk in "
                           "this build" % source_kind)
    return "standing", None


def bench_reasons(profile, source_kind, capabilities, extra_bench=None,
                  supervisor_benched=None):
    """The one bench computation every surface reads.

    Returns {desk: reason} for every benched desk, combining the
    muster benches (extra_bench, including the ephemeris bench), the
    supervisor's mid-watch benches, and the capability and roster
    benches from desk_state. Officer state saves, the status verb,
    the doctor, and the muster desk_health events all read this, so
    no two surfaces can disagree about which desks are down.
    """
    out = {}
    for name in SPECS:
        if extra_bench and name in extra_bench:
            out[name] = extra_bench[name]
        elif supervisor_benched and name in supervisor_benched:
            out[name] = supervisor_benched[name]
        else:
            state, reason = desk_state(name, profile, source_kind,
                                       capabilities)
            if state == "benched":
                out[name] = reason
    return out


# ------------------------------------------------------------- snippets

def _seed_int(key):
    """Fold any seed key (often a tuple) into one deterministic int."""
    h = 1469598103934665603
    parts = key if isinstance(key, tuple) else (key,)
    for p in parts:
        text = p if isinstance(p, str) else repr(p)
        for ch in text:
            h = ((h ^ ord(ch)) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def _wav_bytes(seed_key, seconds=0.5):
    """A short synthetic audio snippet (simulated source evidence)."""
    rate = 8000
    rng = random.Random(_seed_int(seed_key))
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        tone = 9000 * math.sin(2 * math.pi * 440 * i / rate)
        value = int(tone + rng.uniform(-5000, 5000))
        frames += struct.pack("<h", max(-32767, min(32767, value)))
    header = (b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
              + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate,
                                      rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(frames)))
    return bytes(header) + bytes(frames)


def _ppm_bytes(seed_key, width=320, height=200):
    """A synthetic APT-style image: bright bands over noise."""
    rng = random.Random(_seed_int(seed_key))
    rows = bytearray()
    for y in range(height):
        band = 90 if (y // 28) % 2 == 0 else 150
        for _x in range(width):
            v = max(0, min(255, band + rng.randrange(-38, 38)))
            rows += bytes((v, v, v))
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(rows)


def _snippet(data_bytes, filename, kind):
    return {"filename": filename, "kind": kind,
            "b64": base64.b64encode(data_bytes).decode("ascii")}


# ----------------------------------------------------------------- ADS-B

_adsb_pending = {}

# CPR global decode is only valid for an even/odd frame pair received
# within this many seconds of each other (the airborne CPR pairing
# window). A frame older than the window is expired, never paired.
CPR_PAIR_WINDOW_S = 10.0


def reset_state():
    _adsb_pending.clear()


def process_adsb(feed, ctx):
    out = []
    for frame in feed.get("frames", []):
        if not modes.crc_ok(frame):
            continue
        tc = modes.typecode(frame)
        icao = modes.icao(frame)
        if 1 <= tc <= 4:
            callsign = modes.decode_callsign(frame)
            out.append(make_event(
                ctx["watch_id"], "adsb", "decode", ctx["t0"], ctx["t1"],
                freq_hz=1090e6, emitter=icao, grade="CONFIRMED",
                content=callsign,
                data={"emitter_kind": "aircraft", "icao": icao,
                      "callsign": callsign, "also": [callsign],
                      "frame": frame}))
        elif 9 <= tc <= 18:
            parity = modes.frame_parity(frame)
            slot = _adsb_pending.setdefault(icao, {})
            # Expire any pending frame outside the pairing window
            # before it can stitch a stale global position.
            for p in list(slot):
                if abs(ctx["t0"] - slot[p][1]) > CPR_PAIR_WINDOW_S:
                    del slot[p]
            slot[parity] = (frame, ctx["t0"])
            partner = slot.get(1 - parity)
            if partner is not None:
                pos = modes.decode_position(frame, partner[0])
                grade, how = "CONFIRMED", "cpr-global"
            else:
                pos = modes.decode_position(frame, ref=ctx["home"])
                grade, how = "PROBABLE", "cpr-local-reference"
            if pos is None:
                continue
            out.append(make_event(
                ctx["watch_id"], "adsb", "telemetry", ctx["t0"], ctx["t1"],
                freq_hz=1090e6, emitter=icao, grade=grade,
                data={"emitter_kind": "aircraft", "icao": icao,
                      "lat": pos[0], "lon": pos[1],
                      "alt_ft": modes.decode_altitude(frame),
                      "cpr": how, "frame": frame}))
        elif tc == 19:
            vel = modes.decode_velocity(frame)
            if vel:
                out.append(make_event(
                    ctx["watch_id"], "adsb", "telemetry", ctx["t0"],
                    ctx["t1"], freq_hz=1090e6, emitter=icao,
                    grade="CONFIRMED",
                    data={"emitter_kind": "aircraft", "icao": icao,
                          "velocity": vel, "frame": frame}))
    return out


# --------------------------------------------------------------- airband

def process_airband(feed, ctx):
    return _process_voice(feed, ctx, "airband")


def process_milair(feed, ctx):
    return _process_voice(feed, ctx, "milair")


def _process_voice(feed, ctx, desk):
    out = []
    guard = ctx["profile"]["flash"]["guard_hz"]
    for seg in feed.get("segments", []):
        is_guard = desk == "airband" and abs(seg["freq_hz"] - guard) < 1.0
        data = {"emitter_kind": "aircraft", "snr_db": seg["snr_db"],
                "duration_s": seg["dur_s"]}
        emitter = None
        grade = "UNRESOLVED"
        if seg.get("callsign"):
            emitter = seg["callsign"]
            grade = "PROBABLE"
            data["also"] = [seg["callsign"]]
            data["heard_callsign"] = seg["callsign"]
        ev = make_event(
            ctx["watch_id"], desk, "alert" if is_guard else "voice",
            ctx["t0"] + seg["offset_s"],
            ctx["t0"] + seg["offset_s"] + seg["dur_s"],
            freq_hz=seg["freq_hz"], emitter=emitter, grade=grade,
            content=("activity on the aviation guard frequency"
                     if is_guard else None), data=data)
        if seg["snr_db"] >= 18 or is_guard:
            if seg.get("audio_b64"):
                # Real source: the snippet is the captured audio
                # itself, carried in the feed by the Tuner Server.
                ev["data"]["_snippet"] = {
                    "filename": "%s-%d-%d.wav"
                                % (desk, int(seg["freq_hz"]),
                                   int(seg["offset_s"])),
                    "kind": "voice", "b64": seg["audio_b64"]}
            else:
                ev["data"]["_snippet"] = _snippet(
                    _wav_bytes((ctx["watch_id"], seg["freq_hz"],
                                seg["offset_s"])),
                    "%s-%d-%d.wav" % (desk, int(seg["freq_hz"]),
                                      int(seg["offset_s"])),
                    "voice")
        out.append(ev)
    return out


# ----------------------------------------------------------------- ACARS

def process_acars(feed, ctx):
    out = []
    for raw in feed.get("messages", []):
        msg = codecs.acars_parse(raw)
        if msg is None:
            continue
        out.append(make_event(
            ctx["watch_id"], "acars", "decode", ctx["t0"], ctx["t1"],
            freq_hz=131.55e6, emitter=msg["tail"], grade="CONFIRMED",
            content=msg["text"],
            data={"emitter_kind": "aircraft", "tail": msg["tail"],
                  "flight": msg["flight"], "label": msg["label"],
                  "also": [msg["flight"]], "raw": raw}))
    return out


# ------------------------------------------------------------------- AIS

def process_ais(feed, ctx):
    out = []
    for sentence in feed.get("sentences", []):
        try:
            dec = codecs.ais_decode(sentence)
        except ValueError:
            continue
        if dec["type"] in (1, 2, 3):
            out.append(make_event(
                ctx["watch_id"], "ais", "telemetry", ctx["t0"], ctx["t1"],
                freq_hz=162.025e6, emitter=dec["mmsi"], grade="CONFIRMED",
                data={"emitter_kind": "vessel", "mmsi": dec["mmsi"],
                      "lat": dec["lat"], "lon": dec["lon"],
                      "sog_kt": dec["sog_kt"], "cog_deg": dec["cog_deg"]}))
        elif dec["type"] == 5:
            out.append(make_event(
                ctx["watch_id"], "ais", "decode", ctx["t0"], ctx["t1"],
                freq_hz=162.025e6, emitter=dec["mmsi"], grade="CONFIRMED",
                content=dec["name"],
                data={"emitter_kind": "vessel", "mmsi": dec["mmsi"],
                      "name": dec["name"], "callsign": dec["callsign"],
                      "also": [dec["callsign"]],
                      "destination": dec["destination"]}))
    return out


# -------------------------------------------------------------- wx radio

def process_wxradio(feed, ctx):
    out = []
    if feed.get("present"):
        # Presence here is signal energy over a threshold, not a
        # decoded broadcast: it files as a PROBABLE census. CONFIRMED
        # is reserved for an actually decoded SAME header, below.
        data = {"emitter_kind": "station"}
        if feed.get("same_note"):
            data["same_note"] = feed["same_note"]
        out.append(make_event(
            ctx["watch_id"], "wxradio", "census", ctx["t0"], ctx["t1"],
            freq_hz=feed.get("freq_hz", 162.475e6),
            emitter="NOAA Weather Radio", grade="PROBABLE",
            content="weather radio broadcast present (signal energy; "
                    "no SAME header decoded this dwell)",
            data=data))
    for header in feed.get("same", []):
        parsed = codecs.same_parse(header)
        if parsed is None:
            continue
        out.append(make_event(
            ctx["watch_id"], "wxradio", "alert", ctx["t0"], ctx["t1"],
            freq_hz=feed.get("freq_hz", 162.475e6),
            emitter=parsed["station"], grade="CONFIRMED",
            content="%s for areas %s" % (parsed["event"],
                                         ", ".join(parsed["areas"])),
            data={"emitter_kind": "station", "same": parsed,
                  "raw": header, "flash": True}))
    return out


# ------------------------------------------------------------------- ISM

def process_ism(feed, ctx):
    out = []
    for burst in feed.get("bursts", []):
        out.append(make_event(
            ctx["watch_id"], "ism", "decode", ctx["t0"], ctx["t1"],
            freq_hz=burst["freq_hz"], emitter=burst["device"],
            grade="CONFIRMED", data={
                "emitter_kind": "device", "model": burst["model"],
                "reading": burst["data"], "rssi_dbm": burst["rssi_dbm"]}))
    return out


# ------------------------------------------------------------------ APRS

def process_aprs(feed, ctx):
    out = []
    for packet in feed.get("packets", []):
        parsed = codecs.aprs_parse(packet)
        if parsed is None:
            continue
        data = {"emitter_kind": "station", "raw": packet}
        if parsed["kind"] == "position":
            data.update({"lat": parsed["lat"], "lon": parsed["lon"]})
        out.append(make_event(
            ctx["watch_id"], "aprs",
            "telemetry" if parsed["kind"] == "position" else "decode",
            ctx["t0"], ctx["t1"], freq_hz=144.39e6,
            emitter=parsed["source"], grade="CONFIRMED",
            content=parsed.get("comment") or None, data=data))
    return out


# ----------------------------------------------------------------- pager

def process_pager(feed, ctx):
    out = []
    for batch in feed.get("batches", []):
        dec = codecs.pocsag_decode_batch(batch["hex"], batch["frame"])
        if dec is None:
            continue
        address, function, message = dec
        out.append(make_event(
            ctx["watch_id"], "pager", "decode", ctx["t0"], ctx["t1"],
            freq_hz=929.6125e6, emitter="PAGER:%d" % address,
            grade="CONFIRMED", content=message,
            data={"emitter_kind": "pager", "address": address,
                  "function": function, "broadcast": True}))
    # Real source: messages already decoded from the audio by an
    # external decoder program (multimon-ng) and carried at the same
    # normalized layer the batch path produces.
    for msg in feed.get("messages", []):
        out.append(make_event(
            ctx["watch_id"], "pager", "decode", ctx["t0"], ctx["t1"],
            freq_hz=929.6125e6, emitter="PAGER:%d" % msg["address"],
            grade="CONFIRMED", content=msg["message"],
            data={"emitter_kind": "pager", "address": msg["address"],
                  "function": msg.get("function", 0),
                  "broadcast": True,
                  "decoder": msg.get("decoder", "multimon-ng")}))
    return out


# ------------------------------------------------------------- satellite

def process_satellite(feed, ctx):
    p = ctx.get("pass") or {}
    base = {"emitter_kind": "satellite", "sat": feed.get("sat"),
            "norad": feed.get("norad"),
            "prediction_grade": "PROBABLE",
            "pass": {k: p.get(k) for k in
                     ("aos", "los", "t_max", "max_elev_deg",
                      "aos_az_deg", "los_az_deg")}}
    if feed.get("lock"):
        data = dict(base, quality=feed.get("quality"))
        if feed.get("audio_b64"):
            # Real source: the evidence is the captured pass audio.
            # APT imagery decode needs a decoder program this build
            # does not bind, and the desk says so rather than filing
            # an image it did not receive.
            data["audio"] = feed.get("audio_name")
            data["imagery"] = None
            ev = make_event(
                ctx["watch_id"], "satellite", "pass", ctx["t0"],
                ctx["t1"], freq_hz=ctx.get("freq_hz"),
                emitter=feed.get("sat"), grade="CONFIRMED",
                content="pass captured, signal quality %.2f; pass "
                        "audio filed as evidence - APT imagery is "
                        "not decoded by this build"
                        % feed.get("quality", 0),
                data=data)
            ev["data"]["_snippet"] = {
                "filename": feed.get("audio_name", "pass.wav"),
                "kind": "satellite", "b64": feed["audio_b64"]}
            return [ev]
        ev = make_event(
            ctx["watch_id"], "satellite", "pass", ctx["t0"], ctx["t1"],
            freq_hz=ctx.get("freq_hz"), emitter=feed.get("sat"),
            grade="CONFIRMED",
            content="pass captured, decode quality %.2f"
                    % feed.get("quality", 0),
            data=dict(data, imagery="sat-%s-%d.ppm" % (feed.get("norad"),
                                                       int(ctx["t0"]))))
        ev["data"]["_snippet"] = _snippet(
            _ppm_bytes((feed.get("sat"), int(ctx["t0"]))),
            ev["data"]["imagery"], "satellite")
        return [ev]
    return [make_event(
        ctx["watch_id"], "satellite", "pass", ctx["t0"], ctx["t1"],
        freq_hz=ctx.get("freq_hz"), emitter=feed.get("sat"),
        grade="UNRESOLVED",
        content="predicted pass produced no lock",
        data=dict(base, failure="no lock"))]


# ----------------------------------------------------------------- sonde

def process_sonde(feed, ctx):
    out = []
    by_serial = {}
    for frame in feed.get("frames", []):
        by_serial.setdefault(frame["serial"], []).append(frame)
        out.append(make_event(
            ctx["watch_id"], "sonde", "telemetry", ctx["t0"], ctx["t1"],
            freq_hz=402.5e6, emitter=frame["serial"], grade="CONFIRMED",
            data={"emitter_kind": "sonde", "lat": frame["lat"],
                  "lon": frame["lon"], "alt_m": frame["alt_m"],
                  "site": frame["site"]}))
    for serial, frames in by_serial.items():
        if any(f["burst"] for f in frames):
            out.append(make_event(
                ctx["watch_id"], "sonde", "pass", ctx["t0"], ctx["t1"],
                freq_hz=402.5e6, emitter=serial, grade="CONFIRMED",
                content="sonde flight complete, burst reached",
                data={"emitter_kind": "sonde", "flash": True,
                      "site": frames[0]["site"],
                      "max_alt_m": max(f["alt_m"] for f in frames)}))
    # Real source: the window is censused and carriers in the sonde
    # band are reported as presence. Telemetry decode needs an
    # RS41-class decoder program this build does not bind, and the
    # event says so rather than inventing a track.
    for carrier in feed.get("carriers", []):
        out.append(make_event(
            ctx["watch_id"], "sonde", "census", ctx["t0"], ctx["t1"],
            freq_hz=carrier["freq_hz"], emitter=None, grade="PROBABLE",
            content="carrier present in the radiosonde band during "
                    "the sonde window; telemetry is not decoded - "
                    "an RS41-class decoder is not bound in this build",
            data={"emitter_kind": "sonde", "telemetry": False,
                  "peak_dbm": carrier.get("peak_dbm"),
                  "noise_floor_db": carrier.get("noise_floor_db")}))
    return out


# ------------------------------------------------------------------ Tier 3

def process_cellular(feed, ctx):
    return [make_event(
        ctx["watch_id"], "cellular", "census", ctx["t0"], ctx["t1"],
        freq_hz=cell["freq_hz"], emitter=cell["cell_id"],
        grade=cell.get("grade", "CONFIRMED"),
        content="cell present, %s, RSRP %.1f dBm (metadata only)"
                % (cell["band"], cell["rsrp_dbm"]),
        data={"emitter_kind": "cell", "band": cell["band"],
              "rsrp_dbm": cell["rsrp_dbm"], "metadata_only": True})
        for cell in feed.get("cells", [])]


def process_trunking(feed, ctx):
    out = []
    for s in feed.get("systems", []):
        if s.get("activity_only"):
            # Real source: a persistent carrier in the trunking band,
            # censused as presence. System identity, sites and
            # talkgroup counts need a trunking decoder this build
            # does not bind, and the event says so.
            out.append(make_event(
                ctx["watch_id"], "trunking", "census", ctx["t0"],
                ctx["t1"], freq_hz=s["freq_hz"],
                emitter="SYS:" + s["sysid"], grade="PROBABLE",
                content="persistent carrier in the trunking band, "
                        "consistent with a control channel (metadata "
                        "only); system identity and talkgroup counts "
                        "are not decoded by this build",
                data={"emitter_kind": "system", "sysid": s["sysid"],
                      "metadata_only": True,
                      "peak_dbm": s.get("peak_dbm")}))
            continue
        out.append(make_event(
            ctx["watch_id"], "trunking", "census", ctx["t0"], ctx["t1"],
            freq_hz=s["freq_hz"], emitter="SYS:" + s["sysid"],
            grade="CONFIRMED",
            content="trunked system %s, %d sites, %d talkgroups active "
                    "(metadata only)" % (s["sysid"], s["sites"],
                                         s["talkgroups_active"]),
            data={"emitter_kind": "system", "sysid": s["sysid"],
                  "sites": s["sites"],
                  "talkgroups_active": s["talkgroups_active"],
                  "metadata_only": True}))
    return out


def process_broadcast(feed, ctx):
    out = []
    for st in feed.get("stations", []):
        out.append(make_event(
            ctx["watch_id"], "broadcast", "census", ctx["t0"], ctx["t1"],
            freq_hz=st["freq_hz"], emitter=st.get("rds"),
            grade="CONFIRMED" if st.get("rds") else "PROBABLE",
            content="FM station on the air%s"
                    % (", RDS %s" % st["rds"] if st.get("rds") else ""),
            data={"emitter_kind": "station",
                  "peak_dbm": st.get("peak_dbm")}))
    return out


# ----------------------------------------------------------------- sweep

def process_sweep(feed, ctx):
    out = []
    floor = feed.get("noise_floor_db")
    for em in feed.get("emissions", []):
        label = em.get("label")
        unknown = (em.get("shape") or "").startswith("unknown")
        data = {"emitter_kind": "emission", "shape": em.get("shape"),
                "bw_hz": em.get("bw_hz"), "peak_dbm": em.get("peak_dbm"),
                "noise_floor_db": floor, "band": band_name(em["freq_hz"]),
                "unknown_shape": unknown}
        if floor is not None and em.get("peak_dbm") is not None:
            data["snr_db"] = round(em["peak_dbm"] - floor, 1)
            if data["snr_db"] >= 30:
                data["interrupt_candidate"] = True
        out.append(make_event(
            ctx["watch_id"], "sweep", "observation", ctx["t0"], ctx["t1"],
            freq_hz=em["freq_hz"], emitter=label,
            grade="UNRESOLVED" if unknown else "PROBABLE",
            content=label, data=data))
    return out


# ---------------------------------------------------------- audio census

def _samples_wav(samples, rate=16000):
    import io as _io
    import struct as _struct
    import wave as _wave
    buf = _io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(_struct.pack("<%dh" % len(samples), *samples))
    return buf.getvalue()


def process_audiocensus(feed, ctx):
    """The universal audio census desk: every audio-bearing
    emission the window held, demodulated and graded by the
    census engine in the source feed. Unknown audio is reported
    as unknown audio; a grade is a machine estimate, and no event
    here claims decoded speech."""
    out = []
    for cand in feed.get("candidates", []):
        grade_letter = cand.get("grade", "D")
        if grade_letter is None:
            out.append(make_event(
                ctx["watch_id"], "audiocensus", "observation",
                ctx["t0"], ctx["t1"], freq_hz=cand["freq_hz"],
                emitter="AUDIO:%.3f MHz" % (cand["freq_hz"] / 1e6),
                grade="UNRESOLVED",
                content="audio-band emission detected (SNR %s dB, "
                        "about %s Hz wide); extraction deferred "
                        "under the per-window extraction cap"
                        % (cand.get("snr_db"), cand.get("bw_hz")),
                data={"emitter_kind": "audio_emission",
                      "snr_db": cand.get("snr_db"),
                      "bw_hz": cand.get("bw_hz"),
                      "extraction_deferred": True}))
            continue
        emitter = "AUDIO:%.3f MHz" % (cand["freq_hz"] / 1e6)
        data = {"emitter_kind": "audio_emission",
                "modulation_estimate": cand.get("modulation"),
                "voice_grade": grade_letter,
                "voice_score": cand.get("score", 0),
                "snr_db": cand.get("snr_db"),
                "bw_hz": cand.get("bw_hz"),
                "duration_s": cand.get("duration_s", 0.0)}
        if cand.get("grade_parts"):
            data["grade_parts"] = cand["grade_parts"]
        if grade_letter == "DATA":
            ev = make_event(
                ctx["watch_id"], "audiocensus", "observation",
                ctx["t0"], ctx["t1"], freq_hz=cand["freq_hz"],
                emitter=emitter, grade="PROBABLE",
                content="data emission (constant-envelope, discrete "
                        "tones) found by the audio census; not "
                        "voice and not graded as voice",
                data=data)
        elif grade_letter in ("A", "B", "C"):
            ev = make_event(
                ctx["watch_id"], "audiocensus", "audio",
                ctx["t0"], ctx["t1"], freq_hz=cand["freq_hz"],
                emitter=emitter,
                grade="PROBABLE" if grade_letter in ("A", "B")
                else "UNRESOLVED",
                content="unknown audio emission: modulation "
                        "estimated %s, voice likelihood grade %s "
                        "(%d/100, machine estimate), SNR %s dB, "
                        "about %s s in the sampled slice; audio "
                        "filed, not decoded by any named desk"
                        % (cand.get("modulation"), grade_letter,
                           cand.get("score", 0), cand.get("snr_db"),
                           cand.get("duration_s", 0.0)),
                data=data)
            if cand.get("audio"):
                ev["data"]["_snippet"] = _snippet(
                    _samples_wav(cand["audio"]),
                    "audiocensus-%d.wav" % int(cand["freq_hz"]),
                    "voice")
                if grade_letter == "A":
                    ev["data"]["transcribe_candidate"] = True
        else:
            ev = make_event(
                ctx["watch_id"], "audiocensus", "observation",
                ctx["t0"], ctx["t1"], freq_hz=cand["freq_hz"],
                emitter=emitter, grade="UNRESOLVED",
                content="audio-band energy graded D by the audio "
                        "census (score %d/100): consistent with "
                        "noise or a burst, no snippet filed"
                        % cand.get("score", 0),
                data=data)
        out.append(ev)
    if feed.get("carriers"):
        out.append(make_event(
            ctx["watch_id"], "audiocensus", "observation",
            ctx["t0"], ctx["t1"], freq_hz=ctx.get("freq_hz"),
            grade="UNRESOLVED",
            content="%d narrow carrier(s) under 400 Hz wide in "
                    "this window: census log only, no audio "
                    "extracted" % feed["carriers"],
            data={"emitter_kind": "carrier",
                  "carriers": feed["carriers"]}))
    if feed.get("over_cap"):
        out.append(make_event(
            ctx["watch_id"], "audiocensus", "observation",
            ctx["t0"], ctx["t1"], freq_hz=ctx.get("freq_hz"),
            grade="CONFIRMED",
            content="%d further emission(s) in this window were "
                    "census-counted only: the per-window "
                    "extraction cap was reached"
                    % feed["over_cap"],
            data={"emitter_kind": "audio_emission",
                  "over_cap": feed["over_cap"]}))
    return out


# ------------------------------------------------------------- amateur

def process_amateur(feed, ctx):
    out = []
    for sig in feed.get("cw", []):
        text = sig.get("text", "")
        emitter = None
        import re as _re
        m = _re.search(r"DE ([A-Z0-9/]{3,})", text)
        if m:
            emitter = m.group(1)
        out.append(make_event(
            ctx["watch_id"], "amateur", "decode", ctx["t0"], ctx["t1"],
            freq_hz=sig.get("freq_hz"), emitter=emitter,
            grade="PROBABLE",
            content="CW decoded at about %s wpm (machine decode, "
                    "element confidence %s; unresolved characters "
                    "are marked ?): %s"
                    % (sig.get("wpm"), sig.get("confidence"), text),
            data={"emitter_kind": "amateur_station",
                  "mode": "CW", "snr_db": sig.get("snr_db"),
                  "wpm": sig.get("wpm"),
                  "confidence": sig.get("confidence"),
                  "text": text}))
    for dec in feed.get("ft8", []):
        out.append(make_event(
            ctx["watch_id"], "amateur", "decode", ctx["t0"], ctx["t1"],
            freq_hz=dec.get("freq_hz"), grade="CONFIRMED",
            content="FT8 decoded by the bound jt9 program: %s"
                    % dec.get("text"),
            data={"emitter_kind": "amateur_station", "mode": "FT8",
                  "snr_db": dec.get("snr_db"), "dt_s": dec.get("dt_s"),
                  "text": dec.get("text")}))
    return out


# ----------------------------------------------------------------- ISS

def process_iss(feed, ctx):
    out = []
    p = ctx.get("pass") or {}
    sat = p.get("sat", "ISS")
    for seg in feed.get("segments", []):
        ev = make_event(
            ctx["watch_id"], "iss", "voice",
            ctx["t0"] + seg.get("offset_s", 0),
            ctx["t0"] + seg.get("offset_s", 0) + seg.get("dur_s", 0),
            freq_hz=145.8e6, emitter=sat, grade="PROBABLE",
            content="voice activity on the ISS downlink during "
                    "the pass; audio filed as a snippet, not "
                    "transcribed by this desk",
            data={"emitter_kind": "satellite", "sat": sat,
                  "snr_db": seg.get("snr_db"),
                  "duration_s": seg.get("dur_s")})
        if seg.get("audio_b64"):
            ev["data"]["_snippet"] = {
                "filename": "iss-%d.wav" % int(seg.get("offset_s", 0)),
                "kind": "voice", "b64": seg["audio_b64"]}
        out.append(ev)
    img = feed.get("sstv")
    if img:
        ev = make_event(
            ctx["watch_id"], "iss", "decode", ctx["t0"], ctx["t1"],
            freq_hz=145.8e6, emitter=sat, grade="CONFIRMED",
            content="SSTV image decoded from the ISS downlink: "
                    "%s, %d of %d lines recovered; the image is "
                    "filed as received, unretouched"
                    % (img.get("mode"), img.get("lines", 0),
                       img.get("height", 0)),
            data={"emitter_kind": "satellite", "sat": sat,
                  "sstv_mode": img.get("mode"),
                  "lines": img.get("lines")})
        import base64 as _b64
        ev["data"]["_snippet"] = {
            "filename": "iss-sstv-%d.png" % int(ctx["t0"]),
            "kind": "image",
            "b64": _b64.b64encode(img["png"]).decode("ascii")
            if isinstance(img.get("png"), (bytes, bytearray))
            else img.get("png_b64")}
        out.append(ev)
    if not out and feed.get("attempted"):
        out.append(make_event(
            ctx["watch_id"], "iss", "observation", ctx["t0"],
            ctx["t1"], freq_hz=145.8e6, emitter=sat,
            grade="UNRESOLVED",
            content="ISS pass flown with the receiver on the "
                    "downlink; no voice and no SSTV decoded this "
                    "pass (the station is silent on most passes)",
            data={"emitter_kind": "satellite", "sat": sat}))
    return out


# ----------------------------------------------------------------- VDL2

def process_vdl2(feed, ctx):
    out = []
    for msg in feed.get("messages", []):
        out.append(make_event(
            ctx["watch_id"], "vdl2", "decode", ctx["t0"], ctx["t1"],
            freq_hz=136.975e6, emitter=msg.get("tail"),
            grade="CONFIRMED",
            content="VDL2 decoded by the bound dumpvdl2 program: "
                    "%s" % msg.get("text"),
            data={"emitter_kind": "aircraft",
                  "tail": msg.get("tail"),
                  "text": msg.get("text"), "raw": msg.get("raw")}))
    for burst in feed.get("bursts", []):
        out.append(make_event(
            ctx["watch_id"], "vdl2", "observation", ctx["t0"],
            ctx["t1"], freq_hz=136.975e6, grade="UNRESOLVED",
            content="VDL2-shaped burst energy on 136.975 MHz "
                    "(burst census; no frame decoded in this "
                    "capture)", data={"emitter_kind": "aircraft",
                                       "burst": burst}))
    return out


# --------------------------------------------------------- digital voice

def process_digitalvoice(feed, ctx):
    """Metadata only, by design and by licence: this desk reports
    the signalling a DSD-FME-class program decodes - system,
    talkgroup, source, slot, encryption flag. It does not decode
    voice: intelligible DMR/P25/NXDN voice needs an AMBE-family
    vocoder this build does not carry, and no event here claims
    otherwise."""
    out = []
    for call in feed.get("calls", []):
        enc = "encrypted" if call.get("encrypted") else "in the clear"
        out.append(make_event(
            ctx["watch_id"], "digitalvoice", "decode", ctx["t0"],
            ctx["t1"], freq_hz=call.get("freq_hz"),
            emitter=call.get("system"), grade="CONFIRMED",
            content="%s signalling decoded (metadata only): "
                    "talkgroup %s, source %s, slot %s, call %s; "
                    "voice is not decoded by this build - no "
                    "AMBE vocoder is bound"
                    % (call.get("protocol"), call.get("talkgroup"),
                       call.get("source"), call.get("slot"), enc),
            data={"emitter_kind": "radio_system",
                  "protocol": call.get("protocol"),
                  "talkgroup": call.get("talkgroup"),
                  "source": call.get("source"),
                  "slot": call.get("slot"),
                  "encrypted": bool(call.get("encrypted")),
                  "voice_decoded": False}))
    return out


PROCESS = {
    "adsb": process_adsb,
    "airband": process_airband,
    "milair": process_milair,
    "acars": process_acars,
    "ais": process_ais,
    "wxradio": process_wxradio,
    "ism": process_ism,
    "aprs": process_aprs,
    "pager": process_pager,
    "satellite": process_satellite,
    "sonde": process_sonde,
    "cellular": process_cellular,
    "trunking": process_trunking,
    "broadcast": process_broadcast,
    "sweep": process_sweep,
    "audiocensus": process_audiocensus,
    "amateur": process_amateur,
    "iss": process_iss,
    "vdl2": process_vdl2,
    "digitalvoice": process_digitalvoice,
}
