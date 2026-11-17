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

AIRBAND_CHANNELS = [118.700e6, 119.100e6, 121.500e6, 124.350e6,
                    126.075e6, 127.800e6]

# name -> (tier, service, cpu class, channels [(freq, bw)] or "window")
SPECS = {
    "adsb":      (1, "ADS-B aircraft", "light",
                  [(1090e6, 2e6)]),
    "airband":   (1, "Airband voice", "moderate",
                  [(f, 25e3) for f in AIRBAND_CHANNELS]),
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
# profile says: the decode needs a program pure Python does not carry.
ALWAYS_BENCHED = {
    "vdl2": "VDL2 decode needs a dumpvdl2-class decoder program, which "
            "this standard-library build does not bind",
    "amateur": "SSB, CW and FT8 decode need a WSJT-X-class decoder, "
               "outside standard-library scope; HF spectrum is still "
               "censused by the sweep desk on every HF dwell",
    "digitalvoice": "DMR, P25 and NXDN decode need a DSD-class vocoder, "
                    "which this build does not bind; trunked systems "
                    "are censused as metadata by the trunking desk",
    "hfdl": "HFDL decode needs a dumphfdl-class decoder program, which "
            "this build does not bind",
    "iss": "ISS voice and SSTV decode need external decoders; ISS "
           "passes are still predicted by the satellite ephemeris and "
           "appear in the watch bill",
}

TRANSCRIPTION_REASON = ("no transcription engine is bound in this "
                        "build; voice audio is filed as snippets and "
                        "transcripts are reported as not produced")


def channels_for(name):
    spec = SPECS[name]
    if spec[3] == "window":
        return [(None, None)]
    return list(spec[3])


def desk_state(name, profile, source_kind, capabilities):
    """('standing', None) or ('benched', plain-word reason)."""
    if name in ALWAYS_BENCHED:
        return "benched", ALWAYS_BENCHED[name]
    if name in profile["desks"]["bench"]:
        return "benched", "benched by the watch profile"
    if name not in profile["desks"]["stand"]:
        return "benched", "not in the profile desk roster"
    if capabilities is not None and name not in capabilities:
        return "benched", ("the %s source cannot feed this desk in "
                           "this build" % source_kind)
    return "standing", None


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
            slot[parity] = frame
            partner = slot.get(1 - parity)
            if partner is not None:
                pos = modes.decode_position(frame, partner)
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
    out = []
    guard = ctx["profile"]["flash"]["guard_hz"]
    for seg in feed.get("segments", []):
        is_guard = abs(seg["freq_hz"] - guard) < 1.0
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
            ctx["watch_id"], "airband", "alert" if is_guard else "voice",
            ctx["t0"] + seg["offset_s"],
            ctx["t0"] + seg["offset_s"] + seg["dur_s"],
            freq_hz=seg["freq_hz"], emitter=emitter, grade=grade,
            content=("activity on the aviation guard frequency"
                     if is_guard else None), data=data)
        if seg["snr_db"] >= 18 or is_guard:
            ev["data"]["_snippet"] = _snippet(
                _wav_bytes((ctx["watch_id"], seg["freq_hz"],
                            seg["offset_s"])),
                "airband-%d-%d.wav" % (int(seg["freq_hz"]),
                                       int(seg["offset_s"])), "voice")
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
        out.append(make_event(
            ctx["watch_id"], "wxradio", "census", ctx["t0"], ctx["t1"],
            freq_hz=feed.get("freq_hz", 162.475e6),
            emitter="NOAA Weather Radio", grade="CONFIRMED",
            content="weather radio broadcast present",
            data={"emitter_kind": "station"}))
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
        ev = make_event(
            ctx["watch_id"], "satellite", "pass", ctx["t0"], ctx["t1"],
            freq_hz=ctx.get("freq_hz"), emitter=feed.get("sat"),
            grade="CONFIRMED",
            content="pass captured, decode quality %.2f"
                    % feed.get("quality", 0),
            data=dict(base, quality=feed.get("quality"),
                      imagery="sat-%s-%d.ppm" % (feed.get("norad"),
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
    return out


# ------------------------------------------------------------------ Tier 3

def process_cellular(feed, ctx):
    return [make_event(
        ctx["watch_id"], "cellular", "census", ctx["t0"], ctx["t1"],
        freq_hz=cell["freq_hz"], emitter=cell["cell_id"],
        grade="CONFIRMED",
        content="cell present, %s, RSRP %.1f dBm (metadata only)"
                % (cell["band"], cell["rsrp_dbm"]),
        data={"emitter_kind": "cell", "band": cell["band"],
              "rsrp_dbm": cell["rsrp_dbm"], "metadata_only": True})
        for cell in feed.get("cells", [])]


def process_trunking(feed, ctx):
    return [make_event(
        ctx["watch_id"], "trunking", "census", ctx["t0"], ctx["t1"],
        freq_hz=s["freq_hz"], emitter="SYS:" + s["sysid"],
        grade="CONFIRMED",
        content="trunked system %s, %d sites, %d talkgroups active "
                "(metadata only)" % (s["sysid"], s["sites"],
                                     s["talkgroups_active"]),
        data={"emitter_kind": "system", "sysid": s["sysid"],
              "sites": s["sites"],
              "talkgroups_active": s["talkgroups_active"],
              "metadata_only": True})
        for s in feed.get("systems", [])]


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


PROCESS = {
    "adsb": process_adsb,
    "airband": process_airband,
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
}
