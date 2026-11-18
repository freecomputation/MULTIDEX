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

# HFDL channels, in Hz: transcribed from the dumphfdl system
# table, version 52 (etc/systable.conf in the dumphfdl 1.7.0
# source; 16 ground stations, frequencies in kHz there). The day
# set is every table channel at 11 MHz and above that falls
# inside a day rotation window; the night set is every table
# channel below 11 MHz inside a night window (officer.ROTATION).
# The table's 2.9-3.9 MHz channels and its 15025 kHz channel fall
# outside every window and are not carried. Windows were chosen
# around the United States stations (Riverhead, San Francisco,
# Molokai, Barrow); neighbouring stations' channels that fall in
# the same windows are carried with them.
HFDL_DAY_CHANNELS = [
    11184000, 11306000, 11312000, 11318000, 11321000, 11327000, 11348000,
    11354000, 11384000, 11387000, 13264000, 13270000, 13276000, 13303000,
    13312000, 13315000, 13321000, 13324000, 13342000, 13351000, 17901000,
    17912000, 17916000, 17919000, 17922000, 17928000, 17934000, 17958000,
    17967000, 17985000, 21928000, 21931000, 21934000, 21937000, 21949000,
    21955000, 21982000, 21990000, 21997000,
]
HFDL_NIGHT_CHANNELS = [
    4654000, 4660000, 4681000, 4687000, 5451000, 5502000, 5508000,
    5514000, 5529000, 5538000, 5544000, 5547000, 5583000, 5589000,
    5622000, 5652000, 5655000, 5720000, 6529000, 6532000, 6535000,
    6559000, 6565000, 6589000, 6596000, 6619000, 6628000, 6646000,
    6652000, 6661000, 6712000, 8825000, 8834000, 8843000, 8885000,
    8886000, 8894000, 8912000, 8921000, 8927000, 8936000, 8939000,
    8942000, 8948000, 8957000, 8977000, 10027000, 10030000, 10060000,
    10063000, 10066000, 10081000, 10084000, 10087000, 10093000,
]

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
    "probe":     (3, "Probe of the unexplained", "moderate", "window"),
}

# Desks this build benches with the reason stated, whatever the
# profile says. Empty in this build: every desk stands in person
# or through a bound host tool (see CONDITIONAL_BENCHED and
# toolbind). Kept as the first check in desk_state so a future
# design bench lands in exactly one place.
ALWAYS_BENCHED = {}

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

def _apt_decode(feed):
    """Decode the feed's APT pass audio with the package's own
    APT decoder (multidex.apt).

    Returns (png_bytes, info) on success, or (None, reason) with
    the decoder's plain-word reason. The desk never invents an
    image: a recording with no decodable APT in it files its
    audio and the event states why no image was produced.
    """
    from . import apt as apt_mod
    wav = base64.b64decode(feed["audio_b64"])
    try:
        return apt_mod.decode_wav(wav)
    except apt_mod.AptError as exc:
        return None, str(exc)


def _meteor_ack_event(feed, ctx, base):
    """The METEOR pass event under the acknowledgement policy
    (0.6.0): the pass was predicted and billed, and this event
    is its whole record. Neither source attempts a capture for
    a METEOR pass - no scan recording, no audio, no IQ - so
    there is no lock to report and no evidence to file, and the
    pass carries a zero-byte allocation, stated in the event.
    The wording is the policy statement every METEOR pass
    files, on either source."""
    sat = feed.get("sat") or (ctx.get("pass") or {}).get("sat")
    data = dict(base, sat=sat, acknowledged=True, recorded=False,
                allocation_bytes=0, policy="acknowledgement-only")
    return [make_event(
        ctx["watch_id"], "satellite", "pass", ctx["t0"], ctx["t1"],
        freq_hz=ctx.get("freq_hz"), emitter=sat, grade="CONFIRMED",
        content="pass acknowledged, not recorded: METEOR passes "
                "are acknowledged only under the acknowledgement "
                "policy - no capture was attempted, no audio or "
                "IQ was filed, and the pass carries a zero-byte "
                "reception allocation",
        data=data)]


def process_satellite(feed, ctx):
    p = ctx.get("pass") or {}
    base = {"emitter_kind": "satellite", "sat": feed.get("sat"),
            "norad": feed.get("norad"),
            "prediction_grade": "PROBABLE",
            "pass": {k: p.get(k) for k in
                     ("aos", "los", "t_max", "max_elev_deg",
                      "aos_az_deg", "los_az_deg")}}
    sat_name = feed.get("sat") or p.get("sat") or ""
    if "METEOR" in sat_name.upper():
        # Acknowledgement only, decided before any lock logic:
        # the sources do not capture METEOR passes at all, so a
        # METEOR feed carries no lock and no recording, whatever
        # an older feed shape might still attach to it.
        return _meteor_ack_event(feed, ctx, base)
    if feed.get("lock"):
        data = dict(base, quality=feed.get("quality"))
        quality = feed.get("quality", 0)
        if feed.get("audio_b64"):
            # The evidence is the captured pass audio, filed whole.
            # When the imagery step decodes it, the image is filed
            # with the same event and named in its data.
            data["audio"] = feed.get("audio_name")
            snippets = [{"filename": feed.get("audio_name",
                                              "pass.wav"),
                         "kind": "satellite",
                         "b64": feed["audio_b64"]}]
            sat = (feed.get("sat") or "").upper()
            image_name = "pass-%s-%d.png" % (feed.get("norad"),
                                             int(ctx["t0"]))
            if "NOAA" not in sat:
                data["imagery"] = None
                data["imagery_note"] = (
                    "this satellite's downlink is not classified "
                    "for imagery in this build")
                content = ("pass captured, signal quality %.2f; "
                           "pass audio filed as evidence - imagery "
                           "is not classified for this satellite"
                           % quality)
            else:
                png, outcome = _apt_decode(feed)
                if png is None:
                    data["imagery"] = None
                    data["imagery_note"] = (
                        "APT imagery not decoded: %s" % outcome)
                    content = ("pass captured, signal quality %.2f; "
                               "pass audio filed as evidence - APT "
                               "imagery is not decoded: %s"
                               % (quality, outcome))
                else:
                    data["imagery"] = image_name
                    data["imagery_decode"] = outcome
                    snippets.append({
                        "filename": image_name, "kind": "satellite",
                        "b64": base64.b64encode(png).decode("ascii")})
                    content = ("pass captured, signal quality %.2f; "
                               "APT imagery decoded and filed: %s"
                               % (quality, image_name))
            ev = make_event(
                ctx["watch_id"], "satellite", "pass", ctx["t0"],
                ctx["t1"], freq_hz=ctx.get("freq_hz"),
                emitter=feed.get("sat"), grade="CONFIRMED",
                content=content, data=data)
            ev["data"]["_snippet"] = (snippets[0]
                                      if len(snippets) == 1
                                      else snippets)
            return [ev]
        # A lock whose feed carried no audio: nothing to file and
        # nothing to decode, stated as such. Both sources record on
        # a lock, so this is a feed gap, never a synthetic image.
        data["imagery"] = None
        data["imagery_note"] = "the feed carried no pass audio"
        return [make_event(
            ctx["watch_id"], "satellite", "pass", ctx["t0"], ctx["t1"],
            freq_hz=ctx.get("freq_hz"), emitter=feed.get("sat"),
            grade="CONFIRMED",
            content="pass captured, signal quality %.2f; the feed "
                    "carried no pass audio" % quality,
            data=data)]
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
        ev = make_event(
            ctx["watch_id"], "sweep", "observation", ctx["t0"], ctx["t1"],
            freq_hz=em["freq_hz"], emitter=label,
            grade="UNRESOLVED" if unknown else "PROBABLE",
            content=label, data=data)
        for exc in feed.get("excerpts") or []:
            if exc.get("freq_hz") == em["freq_hz"]:
                # The source channelized this emission's span of
                # the scan capture (tier 2, 0.10.0): the excerpt
                # is the evidence a later classifier reads.
                _attach_excerpt(ev, exc, ctx,
                                "sweep-%d" % int(em["freq_hz"]),
                                em["freq_hz"])
                break
        out.append(ev)
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


def _attach_excerpt(ev, exc, ctx, stem_name, freq_hz):
    """Attach one tier 2 IQ excerpt to an event as a SigMF pair
    (0.10.0): the pair payloads go in FRONT of any payload the
    event already carries, so a voice snippet stays the last
    filed and remains the event's headline snippet - the path
    transcription reads. The event's data names the excerpt's
    data file. Synthetic excerpts (the simulated source's) are
    marked as such in their metadata, as the frame archives
    are."""
    from . import sigmf
    synthetic = (ctx.get("profile") or {}).get(
        "source", {}).get("kind") != "real"
    pair = sigmf.pair_payloads_encoded(
        stem_name, exc["b64"], exc["samples"], exc["rate"],
        freq_hz, ctx["t0"], peak=exc.get("peak", 1.0),
        bw_hz=exc.get("bw_hz"), synthetic=synthetic,
        label="unclassified emission")
    existing = ev["data"].get("_snippet")
    if existing is None:
        ev["data"]["_snippet"] = pair
    else:
        if isinstance(existing, dict):
            existing = [existing]
        ev["data"]["_snippet"] = pair + existing
    ev["data"]["iq_excerpt"] = stem_name + ".sigmf-data"


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
        elif grade_letter == "TONE":
            # A tone is filed as a tone (0.11.0): the grader's
            # tone gate caught one persistent narrow peak with
            # no syllabic modulation, so the event states the
            # tone's frequency and stability and claims no
            # voice. Its source stays unexplained, so the
            # hearing is UNRESOLVED like any other census
            # unknown - and it can earn a probe stare.
            tparts = cand.get("grade_parts") or {}
            data["tone_freq_hz"] = tparts.get("tone_freq_hz")
            data["tone_concentration"] = tparts.get(
                "tone_concentration")
            data["tone_stability"] = tparts.get(
                "spectral_dynamics")
            ev = make_event(
                ctx["watch_id"], "audiocensus", "observation",
                ctx["t0"], ctx["t1"], freq_hz=cand["freq_hz"],
                emitter=emitter, grade="UNRESOLVED",
                content="steady tone heard by the audio census: "
                        "one narrow peak at about %s Hz in the "
                        "demodulated audio and its harmonics hold "
                        "%s of the audio power, with no syllabic "
                        "modulation and no spectral movement; "
                        "filed as a tone, not as possible voice"
                        % (tparts.get("tone_freq_hz"),
                           tparts.get("tone_concentration")),
                data=data)
            if cand.get("excerpt"):
                _attach_excerpt(
                    ev, cand["excerpt"], ctx,
                    "audiocensus-%d" % int(cand["freq_hz"]),
                    cand["freq_hz"])
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
            if cand.get("excerpt"):
                _attach_excerpt(
                    ev, cand["excerpt"], ctx,
                    "audiocensus-%d" % int(cand["freq_hz"]),
                    cand["freq_hz"])
        else:
            content = ("audio-band energy graded D by the audio "
                       "census (score %d/100): consistent with "
                       "noise or a burst, no snippet filed"
                       % cand.get("score", 0))
            if cand.get("excerpt"):
                content = ("audio-band energy graded D by the "
                           "audio census (score %d/100): "
                           "consistent with noise or a burst, no "
                           "audio snippet filed; the emission's "
                           "modulation is unclassified, so a "
                           "channel IQ excerpt is filed as a "
                           "SigMF pair for later classification"
                           % cand.get("score", 0))
            ev = make_event(
                ctx["watch_id"], "audiocensus", "observation",
                ctx["t0"], ctx["t1"], freq_hz=cand["freq_hz"],
                emitter=emitter, grade="UNRESOLVED",
                content=content, data=data)
            if cand.get("excerpt"):
                _attach_excerpt(
                    ev, cand["excerpt"], ctx,
                    "audiocensus-%d" % int(cand["freq_hz"]),
                    cand["freq_hz"])
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


# --------------------------------------------------------------- probe

# The desks each classifier family belongs to, for the probe's
# account of what a vote leaves open.
_FAMILY_DESKS = {
    "am-voice": ("airband", "milair", "amateur"),
    "fm-voice": ("airband", "wxradio", "amateur"),
    "ssb-voice": ("amateur",),
    "fsk-data": ("pager", "aprs", "ism", "acars", "vdl2", "hfdl",
                 "digitalvoice"),
    "cw": ("amateur",),
    "tone": (),
    "noise": (),
}

# Why no in-process attempt is filed for a desk the vote
# leaves open, per desk. An attempt that cannot honestly be
# made is stated, never faked: silence from a desk is not a
# negative result, and neither is a reason it was not tried.
_NOT_ATTEMPTED = {
    "airband": "voice on this desk is filed from its own "
               "channel captures; the audio census attempt on "
               "the stare is the in-process voice attempt this "
               "build can make",
    "milair": "voice on this desk is filed from its own "
              "channel captures; the audio census attempt on "
              "the stare is the in-process voice attempt this "
              "build can make",
    "wxradio": "this desk reads SAME headers from its own "
               "channel captures; the audio census attempt on "
               "the stare is the in-process voice attempt this "
               "build can make",
    "amateur": "the in-process amateur attempt is CW, which "
               "runs on narrowband excerpts; this excerpt is "
               "not narrowband, and SSB voice is the census "
               "attempt's ground",
    "pager": "its decoder reads its own captures through the "
             "host tool path; no in-process attempt from a "
             "channel excerpt exists in this build",
    "aprs": "its decoder reads its own captures through the "
            "host tool path; no in-process attempt from a "
            "channel excerpt exists in this build",
    "ism": "its decoder reads its own captures through the "
           "host tool path; no in-process attempt from a "
           "channel excerpt exists in this build",
    "vdl2": "its decoder is a bound host program that reads "
            "its own captures; it cannot be run against a "
            "channel excerpt in this build",
    "hfdl": "its decoder is a bound host program that reads "
            "its own captures; it cannot be run against a "
            "channel excerpt in this build",
    "digitalvoice": "its decoder is a bound host program that "
                    "reads its own captures; it cannot be run "
                    "against a channel excerpt in this build",
    "acars": "the target is outside the ACARS channels, so "
             "no ACARS attempt is meaningful",
}

_ACARS_CHANNELS = (131.45e6, 131.55e6, 131.725e6)


def _context_sentence(context):
    """The probe capture's context, stated in plain words from
    the feed's own account: how much IQ the source held, and
    how much of it sits before and after the emission, with
    the source's note where it carries one. Nothing is
    implied beyond what the feed states."""
    if not context:
        return "the capture carried no context statement"
    parts = []
    if context.get("capture_s") is not None:
        parts.append("the capture holds %.1f s of IQ"
                     % context["capture_s"])
    if context.get("pre_s") is not None:
        parts.append("%.1f s of it before the emission's onset"
                     % context["pre_s"])
    if context.get("post_s") is not None:
        parts.append("%.1f s after its end" % context["post_s"])
    if context.get("note"):
        parts.append(context["note"])
    return "; ".join(parts) if parts else \
        "the capture carried no context statement"


def process_probe(feed, ctx):
    """The probe desk: one stare at one unexplained emitter.

    The feed is the stare itself (the source's capture at the
    target, its census analysis, and a channel excerpt with
    its context stated). ctx carries the target's full Index
    history, assembled by the officer, with each hearing's
    per-burst family vote and the cross-band coincidence
    count. The desk files, in order: the stare observation
    (the excerpt rides it as a tier 2 SigMF pair), one
    negative-result assessment per desk that attempted a
    decode of the stare and filed none, the dossier-cluster
    vote as an assessment, and the discrimination assessment
    (cross-band coincidence, and recurrence folded on the
    solar and sidereal clocks). Positive attempt outcomes
    join the vote as observations and are named in the vote
    assessment. The desk files no bearing claim: direction
    finding is not part of this build.
    """
    from . import audiocensus as _ac
    from . import classifier as _classifier
    from . import probe as _probe
    out = []
    target = feed.get("target") or {}
    freq = feed.get("target_freq_hz") or ctx.get("freq_hz")
    emitter = target.get("emitter")
    pctx = ctx.get("probe") or {}
    history = pctx.get("history") or []
    exc = feed.get("excerpt")
    context = feed.get("context") or {}
    snr = feed.get("snr_db")
    label = emitter or ("%.3f MHz" % (freq / 1e6) if freq
                        else "an unknown frequency")
    # The stare's own classification, from its excerpt.
    stare = None
    chan = None
    rate = None
    if exc:
        try:
            chan, rate = _classifier.decode_excerpt(exc)
            feats0 = _classifier.extract_features(chan, rate)
            fam0, why0 = _classifier.classify(feats0)
            stare = {"family": fam0,
                     "features": {k: round(float(v), 4)
                                  for k, v in feats0.items()},
                     "reasons": why0}
        except Exception:
            stare = None
    if stare:
        family, feats, reasons = stare["family"], \
            stare["features"], stare["reasons"]
    elif feed.get("silent"):
        family, feats, reasons = "unknown", {}, [
            "the emitter was silent for the whole stare: the "
            "capture holds noise only"]
    else:
        family, feats, reasons = "unknown", {}, [
            "the stare held no classifiable excerpt"]
    stare_ev = make_event(
        ctx["watch_id"], "probe", "observation", ctx["t0"],
        ctx["t1"], freq_hz=freq, emitter=emitter,
        grade="UNRESOLVED",
        content="probe stare at %s for %.0f s, on a target "
                "with %d unresolved hearings in the record: "
                "the feature tree classifies the stare as %s "
                "(%s). Context: %s."
                % (label, ctx["t1"] - ctx["t0"],
                   target.get("observations", len(history)),
                   family,
                   reasons[0] if reasons else "no reason stated",
                   _context_sentence(context)),
        data={"emitter_kind": "probe_target",
              "probe_target": target,
              "classification": {"family": family,
                                 "features": feats,
                                 "reasons": reasons},
              "context": context, "snr_db": snr,
              "bw_hz": target.get("bw_hz"),
              "silent": bool(feed.get("silent"))})
    if exc:
        _attach_excerpt(stare_ev, exc, ctx,
                        "probe-%d" % int(freq), freq)
    out.append(stare_ev)
    sid = stare_ev["id"]
    # The vote's inputs: every hearing of the cluster, then
    # this stare.
    votes = [(h.get("family") or "unknown", h.get("snr_db"))
             for h in history]
    if stare:
        votes.append((family, snr))
    # Decode attempts on the stare, run in process. Each
    # attempt either decodes (a positive, which joins the
    # vote) or files a negative result: a filed attempt is
    # the only kind of failure that counts as one.
    attempted = set()
    positives = []
    negatives = []
    census = feed.get("census")
    if census is not None:
        attempted.add("audiocensus")
        tol = max(25000.0, (target.get("bw_hz") or 0.0) / 2)
        cand = None
        for c in census.get("candidates") or []:
            if freq and abs(c["freq_hz"] - freq) <= tol:
                cand = c
                break
        if cand is not None and cand.get("grade") in (
                "A", "B", "C", "DATA", "TONE"):
            fam = _probe.family_of_event({"data": {
                "modulation_estimate": cand.get("modulation"),
                "voice_grade": cand.get("grade")}})
            votes.append((fam, cand.get("snr_db")))
            positives.append(
                "the audio census classified the stare as %s "
                "(grade %s, SNR %s dB)"
                % (fam, cand.get("grade"), cand.get("snr_db")))
        else:
            negatives.append((
                "audiocensus",
                "the audio census analysis of the stare found "
                "no classifiable candidate at the target "
                "frequency" if cand is None else
                "the audio census graded the stare's energy D "
                "(score %s/100): no classification"
                % cand.get("score", 0)))
    if chan is not None \
            and feats.get("occupied_bw_hz", 1e9) <= 3000.0:
        attempted.add("amateur")
        from . import cw as _cw
        try:
            found = _cw.detect(chan, rate)
        except Exception:
            found = []
        texts = [f for f in found if f.get("text")]
        if texts:
            positives.append(
                "CW decoded in the stare by the amateur path: "
                "%s" % "; ".join(
                    "%r at about %s wpm" % (f["text"][:40],
                                            f.get("wpm"))
                    for f in texts[:2]))
            votes.append(("cw", snr))
        else:
            negatives.append((
                "amateur",
                "no CW signal decoded in the stare excerpt"))
    if chan is not None and freq and any(
            abs(freq - ch) <= 25000.0 for ch in _ACARS_CHANNELS):
        attempted.add("acars")
        from . import acars as _acars
        klass = {"am-voice": "AM", "fm-voice": "FM",
                 "ssb-voice": "USB"}.get(family, "AM")
        try:
            audio = _ac._demod(chan, rate, klass)
            messages = _acars.decode_audio(
                audio, _ac.AUDIO_RATE)
        except Exception:
            messages = []
        if messages:
            positives.append(
                "ACARS decoded in the stare: %d verified "
                "frame(s)" % len(messages))
            votes.append(("fsk-data", snr))
        else:
            negatives.append((
                "acars",
                "no CRC-verified ACARS frame decoded from the "
                "stare's demodulated audio"))
    for desk, detail in negatives:
        out.append(make_event(
            ctx["watch_id"], "probe", "assessment", ctx["t0"],
            ctx["t1"], freq_hz=freq, emitter=emitter,
            grade="CONFIRMED",
            content="negative result: %s attempted a decode "
                    "of the probe stare at %s and filed no "
                    "decode: %s" % (desk, label, detail),
            data={"rule": "probe_negative_result",
                  "attempted_desk": desk, "outcome": "no decode",
                  "detail": detail},
            links=[sid]))
    # The cluster vote, filed as the desk's assessment.
    v = _probe.vote(votes)
    split_txt = ", ".join(
        "%s %d" % (fam, s["count"])
        for fam, s in sorted(v["split"].items()))
    open_families = set(v["split"])
    if v["family"]:
        open_families.discard(v["family"])
    not_attempted = []
    open_desks = set()
    for fam in open_families:
        open_desks.update(_FAMILY_DESKS.get(fam, ()))
    for desk in sorted(open_desks - attempted - {"audiocensus"}):
        not_attempted.append({
            "desk": desk,
            "reason": _NOT_ATTEMPTED.get(
                desk, "no in-process attempt from a channel "
                      "excerpt exists in this build")})
    if v["family"]:
        vote_content = (
            "probe cluster vote on %s: %d observations classify "
            "as %s with %.0f percent of the SNR weight, over "
            "the %.0f percent margin on at least %d "
            "observations (split: %s); the dossier carries %s"
            % (label, v["observations"], v["family"],
               v["share"] * 100, _probe.VOTE_MARGIN * 100,
               _probe.VOTE_MIN_OBSERVATIONS, split_txt,
               v["family"]))
        vote_grade = "PROBABLE"
    else:
        vote_content = (
            "probe cluster vote on %s: %d observations do not "
            "resolve: the leading family %s holds %.0f percent "
            "of the SNR weight against a %.0f percent margin "
            "(split: %s); the dossier stays UNRESOLVED and "
            "the split is the finding"
            % (label, v["observations"], v["leading"],
               v["share"] * 100, _probe.VOTE_MARGIN * 100,
               split_txt))
        vote_grade = "UNRESOLVED"
    if positives:
        vote_content += ". Positive attempts: %s." % "; ".join(
            positives)
    out.append(make_event(
        ctx["watch_id"], "probe", "assessment", ctx["t0"],
        ctx["t1"], freq_hz=freq, emitter=emitter,
        grade=vote_grade, content=vote_content,
        data={"rule": "probe_cluster_vote",
              "baseline": "%d observations in the cluster"
                          % v["observations"],
              "vote": v, "positives": positives,
              "not_attempted": not_attempted},
        links=[sid]))
    # Discrimination: coincidence and the two clocks, over
    # the cluster's full history plus this stare.
    times = [h["t"] for h in history] + [ctx["t0"]]
    rec = _probe.recurrence(times)
    coin = pctx.get("coincidence") or {}
    if not coin.get("windows_checked"):
        coin_txt = ("no earlier hearings to check coincidence "
                    "against")
    elif not coin.get("by_band"):
        coin_txt = ("in %d windows of plus or minus %d s "
                    "around the cluster's hearings, nothing on "
                    "another band fired with this emitter"
                    % (coin["windows_checked"],
                       coin.get("window_s", 120)))
    else:
        coin_txt = ("hearings with another band active within "
                    "plus or minus %d s: %s"
                    % (coin.get("window_s", 120), ", ".join(
                        "%s band in %d window(s)" % (b, n)
                        for b, n in sorted(
                            coin["by_band"].items()))))
    out.append(make_event(
        ctx["watch_id"], "probe", "assessment", ctx["t0"],
        ctx["t1"], freq_hz=freq, emitter=emitter,
        grade="CONFIRMED",
        content="probe discrimination on %s: cross-band "
                "coincidence - %s. Recurrence - solar fold %d "
                "of %d observations inside one hour (share "
                "%.2f), sidereal fold %d of %d (share %.2f): %s"
                % (label, coin_txt,
                   rec["solar"]["in_window"], rec["solar"]["count"],
                   rec["solar"]["share"],
                   rec["sidereal"]["in_window"],
                   rec["sidereal"]["count"],
                   rec["sidereal"]["share"], rec["verdict"]),
        data={"rule": "probe_discrimination",
              "baseline": "%d observations in the cluster"
                          % len(times),
              "recurrence": rec, "coincidence": coin},
        links=[sid]))
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

# The decode gate each archived frame format stands under,
# stated in every event that archives it: the frames are
# evidence, not audio, until the named build exists.
_FRAME_GATE_TEXT = {
    "AMBE+2": ("the frames are archived undecodable until the "
               "in-house synthesizer exists and the US8359197 "
               "gate opens on 2028-05-20"),
    "IMBE": "the frames await the in-house IMBE build",
}


def _frame_archive_parts(archives):
    """Render vocoder frame archives for a desk event: the
    content suffix stating what was archived and the gate it
    stands under, the provenance records for the event data,
    and the payload snippets. An archive is the de-interleaved
    vocoder parameter frames the bound program wrote beside
    its log, in its own container (see multidex.tuner); the
    snippet kind is 'payload' - payload-class evidence, which
    the snippet store never sheds."""
    text = ""
    records = []
    payloads = []
    for arc in archives:
        fmt = arc.get("format") or "AMBE+2"
        gate = _FRAME_GATE_TEXT.get(
            fmt, "the frames await an in-house vocoder build")
        text += ("; vocoder frames archived as payload "
                 "evidence: %d %s parameter frames (%d bytes) "
                 "from %s - %s"
                 % (arc.get("frames", 0), fmt,
                    arc.get("bytes", 0),
                    arc.get("source_name") or "the capture",
                    gate))
        records.append({
            "format": fmt, "container": arc.get("container"),
            "frames": arc.get("frames"),
            "bytes": arc.get("bytes"),
            "param_bits": arc.get("param_bits"),
            "slot": arc.get("slot"),
            "source_name": arc.get("source_name"),
            "association": arc.get("association"),
            "synthetic": bool(arc.get("synthetic"))})
        payloads.append({
            "filename": arc.get("source_name") or "frames.amb",
            "kind": "payload", "b64": arc.get("b64")})
    return text, records, payloads


def _attach_archives(ev, archives):
    """Attach frame archives to an event already made: the
    content statement, the provenance records, and one payload
    snippet per archive."""
    text, records, payloads = _frame_archive_parts(archives)
    ev["content"] = (ev["content"] or "") + text
    ev["data"].setdefault("frame_archives", []).extend(records)
    existing = ev["data"].get("_snippet")
    if existing is None:
        ev["data"]["_snippet"] = (payloads[0]
                                  if len(payloads) == 1
                                  else payloads)
    else:
        if isinstance(existing, dict):
            existing = [existing]
        ev["data"]["_snippet"] = existing + payloads


def process_digitalvoice(feed, ctx):
    """Signalling metadata, and the vocoder frames themselves
    as evidence: this desk reports the signalling a
    DSD-FME-class program decodes - system, talkgroup, source,
    slot, encryption flag - and, since 0.6.0, archives the
    de-interleaved vocoder parameter frames the same program
    writes beside its log as payload-class evidence linked to
    the call's event and the emitter's dossier. It does not
    decode voice: intelligible DMR/P25/NXDN voice needs an
    AMBE-family vocoder this build does not carry, and no
    event here claims otherwise. The archived frames are
    stated for what they are: AMBE+2 frames are archived
    undecodable until the in-house synthesizer exists and the
    US8359197 gate opens on 2028-05-20, and IMBE frames await
    the in-house IMBE build. The bound program writes frames
    only for calls it can hear in the clear (or with a
    keystream), so an encrypted call archives no frames."""
    out = []
    for call in feed.get("calls", []):
        enc = "encrypted" if call.get("encrypted") else "in the clear"
        ev = make_event(
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
                  "voice_decoded": False})
        if call.get("frames"):
            _attach_archives(ev, call["frames"])
        out.append(ev)
    leftovers = feed.get("frame_archives") or []
    if leftovers:
        # Frames the feed could not associate with a call the
        # log parser read. They are still archived - frames not
        # captured are lost - under the capture window's own
        # provenance, never silently attached to a call they
        # may not belong to.
        for arc in leftovers:
            arc.setdefault("association", "capture window")
        if out:
            _attach_archives(out[0], leftovers)
        else:
            ev = make_event(
                ctx["watch_id"], "digitalvoice", "decode",
                ctx["t0"], ctx["t1"], freq_hz=ctx.get("freq_hz"),
                emitter=None, grade="PROBABLE",
                content="vocoder frames archived from a digital "
                        "voice capture whose signalling the log "
                        "parser did not read; voice is not "
                        "decoded by this build",
                data={"emitter_kind": "radio_system",
                      "voice_decoded": False})
            _attach_archives(ev, leftovers)
            out.append(ev)
    return out


# ------------------------------------------------------------------ HFDL

def _hfdl_party(party):
    """Render one src/dst object from a dumphfdl frame: ground
    stations carry a numeric id and, when the system table was
    loaded, a name; aircraft carry a numeric id and, when the
    program's cache knows it, an ac_info block with the ICAO
    address. Anything absent renders as absent."""
    if not isinstance(party, dict):
        return "unknown party", None
    kind = party.get("type") or "party"
    pid = party.get("id")
    label = "%s %s" % (kind, pid if pid is not None else "?")
    if party.get("name"):
        label += " (%s)" % party["name"]
    icao = None
    info = party.get("ac_info")
    if isinstance(info, dict):
        icao = info.get("icao") or info.get("icao_address")
    return label, icao


def process_hfdl(feed, ctx):
    """HFDL frames decoded by the bound dumphfdl program. One
    event per decoded frame: squitters and logon traffic are
    network signalling, data PDUs are the datalink itself. The
    desk reports what the program decoded - frame kind, parties,
    channel - and nothing it did not."""
    out = []
    for msg in feed.get("messages", []):
        pdu = msg.get("pdu") or "frame"
        src_label, src_icao = _hfdl_party(msg.get("src"))
        dst_label, dst_icao = _hfdl_party(msg.get("dst"))
        # Logon-class LPDUs carry the aircraft address at the
        # PDU level (ac_info), not inside a party object.
        _lvl, lvl_icao = _hfdl_party(
            {"type": "Aircraft", "ac_info": msg.get("ac_info")})
        icao = src_icao or dst_icao or lvl_icao
        detail = msg.get("type_name") or pdu.upper()
        out.append(make_event(
            ctx["watch_id"], "hfdl", "decode", ctx["t0"], ctx["t1"],
            freq_hz=msg.get("freq_hz") or ctx.get("freq_hz"),
            emitter=icao, grade="CONFIRMED",
            content="HFDL %s decoded by the bound dumphfdl "
                    "program: %s to %s"
                    % (detail, src_label, dst_label),
            data={"emitter_kind": "aircraft" if icao else "station",
                  "pdu": pdu, "src": msg.get("src"),
                  "dst": msg.get("dst"),
                  "type_name": msg.get("type_name"),
                  "raw": msg.get("raw")}))
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
    "probe": process_probe,
    "amateur": process_amateur,
    "iss": process_iss,
    "vdl2": process_vdl2,
    "digitalvoice": process_digitalvoice,
    "hfdl": process_hfdl,
}
