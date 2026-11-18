"""0.5.0: independence stage 1 - the dependence census and the
own APT decoder.

The census tests hold the binding table honest from the source
side: an AST scan of the package collects every external
program the code actually invokes, and the set must equal the
census table in multidex.toolbind - a binding added without a
census entry fails here, and so does a census entry for a
binding that no longer exists.

The APT tests are a ground-truth proof, not a comparison
against the retired aptdec: the signal is generated in this
file from the published APT format with known pixel content,
and the decoder must reproduce that content - dimensions, sync
and telemetry placement, both channel gradients, and the marker
block - within the stated tolerance. The tolerance is 20 grey
levels (8% of full scale) at 48 kHz: the sync pulses that
calibrate the grey scale are a 1040 Hz square wave riding a
2400 Hz carrier, and their recovered plateaus ripple by about
that much; mid-tones land far inside it.
"""

import ast
import os
import struct
import sys
import zlib

import numpy as np
import pytest

from multidex import apt, gates, toolbind
from multidex.sim import synthetic_apt_samples
from multidex.tuner import _resample_int16
import array

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "multidex")

TOL = 20          # grey levels, of 255 - see the module docstring
FS = 48000


# ------------------------------------------------------------- census

def _str_const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _invoked_programs():
    """Every external program the package source invokes, read
    from the source itself: literal program names at subprocess
    and helper call sites, literal lookups through which /
    tool_available / find_spec, the chime's player table (a
    module-level string tuple iterated into shutil.which), and
    non-stdlib imports (the faster-whisper binding)."""
    found = set()
    sinks = ("run", "Popen", "_run_tool", "_stream_to_file")
    lookups = ("which", "tool_available", "find_spec")
    for fname in sorted(os.listdir(PKG)):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(PKG, fname)) as fh:
            tree = ast.parse(fh.read())
        module_strs = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(
                    node.value, (ast.List, ast.Tuple)):
                vals = [_str_const(e) for e in node.value.elts]
                if vals and all(v is not None for v in vals):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            module_strs[target.id] = vals
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # A bare import names the top-level module it binds.
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in sys.stdlib_module_names \
                            and top not in ("numpy", "multidex"):
                        found.add(top)
                continue
            if isinstance(node, ast.ImportFrom) and node.level == 0 \
                    and node.module:
                # An absolute from-import names its source module;
                # a relative from-import (level > 0) names a sibling
                # of this package, never an external program.
                top = node.module.split(".")[0]
                if top not in sys.stdlib_module_names \
                        and top not in ("numpy", "multidex"):
                    found.add(top)
                continue
            if isinstance(node, ast.For) and isinstance(
                    node.iter, ast.Name) \
                    and node.iter.id in module_strs \
                    and isinstance(node.target, ast.Name):
                var = node.target.id
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and sub.args \
                            and isinstance(sub.args[0], ast.Name) \
                            and sub.args[0].id == var \
                            and isinstance(sub.func, ast.Attribute) \
                            and sub.func.attr == "which":
                        found.update(module_strs[node.iter.id])
                continue
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if isinstance(func, ast.Attribute) \
                    and func.attr in lookups \
                    or isinstance(func, ast.Name) \
                    and func.id in lookups:
                value = _str_const(node.args[0])
                if value:
                    found.add(value)
            is_sink = isinstance(func, ast.Attribute) \
                and func.attr in sinks or isinstance(func, ast.Name) \
                and func.id in sinks
            if is_sink and isinstance(node.args[0],
                                      (ast.List, ast.Tuple)) \
                    and node.args[0].elts:
                value = _str_const(node.args[0].elts[0])
                if value:
                    found.add(value)
    return found


def test_census_matches_the_invoked_programs():
    invoked = _invoked_programs()
    census = {target for _d, target, _k, _u in toolbind.DEPENDENCES}
    assert invoked == census, (
        "invoked but not in the census: %s; in the census but "
        "never invoked: %s"
        % (sorted(invoked - census), sorted(census - invoked)))
    # The desk tool table rides the same census.
    assert {prog for prog, _r in toolbind.TOOL_DESKS.values()} \
        <= census


def test_census_names_the_standing_debts():
    programs = {row["program"] for row in toolbind.dependence_census()}
    for name in ("rtl_sdr", "rtl_fm", "rtl_433", "multimon-ng",
                 "jt9", "dumpvdl2", "dsd-fme", "dumphfdl",
                 "faster-whisper", "chronyc"):
        assert name in programs
    # The retired bindings are gone from the census, not
    # carried as present-tense entries: the APT binding retired
    # by the own decoder in 0.5.0, and the satellite imagery
    # decoder binding retired by the METEOR acknowledgement
    # policy in 0.6.0.
    assert "aptdec" not in programs
    assert "satdump" not in programs


def test_census_presence_agrees_with_the_probes():
    toolbind.reset_cache()
    for display, target, kind, _used_by in toolbind.DEPENDENCES:
        row = [r for r in toolbind.dependence_census()
               if r["program"] == display][0]
        if kind == "module":
            import importlib.util
            assert row["present"] == (
                importlib.util.find_spec(target) is not None)
        else:
            import shutil
            assert row["present"] == (shutil.which(target)
                                      is not None)


def test_doctor_prints_the_census(profile, home):
    text = gates.doctor_text(profile, home)
    assert "Dependences" in text
    for row in toolbind.dependence_census():
        assert row["program"] in text
        assert row["used_by"] in text
    assert text.count("- dependence") >= len(toolbind.DEPENDENCES)
    assert "aptdec" not in text


# ------------------------------------------------- APT ground truth
#
# The generator below is written from the published APT format
# for this test, following the same construction as the 0.3.0
# proof generator (~/workspace/apt-proof/make_synth.py): 2080
# words per line at 4160 words per second; Sync A 39 words of
# 1040 Hz pulses, space 47, image A 909, telemetry A 45, Sync B
# 39 of 832 Hz, space 47, image B 909, telemetry B 45; the video
# envelope AM-modulates a 2400 Hz subcarrier. The picture is a
# horizontal gradient with a white block in the middle rows.

BLACK, WHITE = 0.13, 0.95
WORDS = 4160


def _sync(n_words, freq):
    half = WORDS / (2.0 * freq)
    out = np.empty(n_words)
    pos = 0.0
    for i in range(n_words):
        out[i] = WHITE if (pos % (2 * half)) < half else 0.05
        pos += 1.0
    return out


def _line(row):
    img_a = BLACK + (WHITE - BLACK) * np.linspace(0, 1, 909)
    img_b = img_a[::-1].copy()
    if 8 <= row < 16:
        img_a[400:520] = WHITE
        img_b[400:520] = WHITE
    return np.concatenate([
        _sync(39, 1040), np.full(47, BLACK), img_a,
        np.full(45, 0.5), _sync(39, 832), np.full(47, BLACK),
        img_b, np.full(45, 0.5)])


def _known_signal(seconds=12):
    env = np.concatenate([_line(r) for r in range(seconds * 2)])
    t_env = np.arange(len(env)) / WORDS
    n_out = int(seconds * FS)
    t_out = np.arange(n_out) / FS
    env_up = np.interp(t_out, t_env, env)
    audio = env_up * np.cos(2 * np.pi * 2400 * t_out)
    return (np.clip(audio * 0.9, -1, 1) * 32767).astype("<i2")


def _expected_grey(level):
    """The grey a transmitted envelope level must decode to,
    under the format's own calibration (sync low 0.05, sync
    high 0.95, audio scaled 0.9 in generation)."""
    return 255.0 * (level - 0.05) / (WHITE - 0.05)


def test_apt_ground_truth_48k():
    samples = _known_signal(12)
    pixels, info = apt.decode_samples(samples, FS)
    assert pixels.shape == (24, 2080)
    assert info["lines"] == 24 and info["width"] == 2080
    assert info["sync_correlation"] >= 0.95
    row = pixels[4].astype(float)
    # Sync A placement: high phases white, low phases black.
    high = [row[i] for i in range(39) if (i % 4) < 2]
    low = [row[i] for i in range(39) if (i % 4) >= 2]
    assert np.mean(high) >= 240 and np.mean(low) <= 16
    # Sync B placement (offset 1040, 832 Hz: high at word
    # centres 0, 1, 2 of each 5).
    high_b = [row[1040 + i] for i in range(39) if (i % 5) < 3]
    low_b = [row[1040 + i] for i in range(39) if (i % 5) >= 3]
    assert np.mean(high_b) >= 180 and np.mean(low_b) <= 90
    # Telemetry sits at its transmitted mid level.
    assert abs(np.mean(row[995:1040]) - _expected_grey(0.5)) <= TOL
    assert abs(np.mean(row[2035:2080]) - _expected_grey(0.5)) <= TOL
    # Image A gradient: rising, at the transmitted levels.
    for col in (100, 500, 800):
        level = BLACK + (WHITE - BLACK) * col / 908.0
        assert abs(row[86 + col] - _expected_grey(level)) <= TOL, \
            "image A column %d" % col
    # Image B is the same gradient reversed.
    for col in (100, 500, 800):
        level = BLACK + (WHITE - BLACK) * (908 - col) / 908.0
        assert abs(row[1126 + col] - _expected_grey(level)) <= TOL, \
            "image B column %d" % col
    # The marker block: white in rows 8-15, gradient in row 4.
    assert np.mean(pixels[8:16, 486:606]) >= 245
    assert np.mean(pixels[4, 486:606]) <= 180


def test_apt_ground_truth_png_bytes():
    # The filed artifact itself carries the content: parse the
    # PNG the decoder writes (its rows are filter 0 by
    # construction) and compare against the pixel array.
    samples = _known_signal(12)
    pixels, _info = apt.decode_samples(samples, FS)
    png = apt.encode_png(pixels)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (2080, 24)
    pos = 8
    idat = b""
    while pos < len(png):
        size, tag = struct.unpack(">I4s", png[pos:pos + 8])
        if tag == b"IDAT":
            idat += png[pos + 8:pos + 8 + size]
        pos += 12 + size
    raw = zlib.decompress(idat)
    assert len(raw) == 24 * (1 + 2080)
    for r in range(24):
        assert raw[r * 2081] == 0
        assert raw[r * 2081 + 1:(r + 1) * 2081] == \
            pixels[r].tobytes()


def test_apt_decodes_the_sim_feed():
    samples = np.asarray(synthetic_apt_samples(12, FS), dtype="<i2")
    pixels, info = apt.decode_samples(samples, FS)
    assert pixels.shape == (24, 2080)
    assert np.mean(pixels[8:16, 486:606]) >= 245
    assert abs(np.mean(pixels[4, 995:1040].astype(float))
               - _expected_grey(0.5)) <= TOL


def test_apt_decodes_the_stored_11025_rate():
    # The stored pass rate compresses the sync span further, so
    # the level tolerance widens; placement and the marker are
    # still exact.
    samples = _known_signal(12)
    stored = _resample_int16(array.array("h", samples.tolist()),
                             FS, 11025)
    kept = np.frombuffer(stored.tobytes(), dtype="<i2")
    pixels, info = apt.decode_samples(kept, 11025)
    assert pixels.shape == (24, 2080)
    assert np.mean(pixels[8:16, 486:606]) >= 240
    assert abs(np.mean(pixels[4, 995:1040].astype(float))
               - _expected_grey(0.5)) <= 32


# --------------------------------------------------------- honesty

def test_apt_refuses_recordings_with_no_apt():
    with pytest.raises(apt.AptError, match="no APT sync"):
        apt.decode_samples(np.zeros(FS, dtype="<i2"), FS)
    rng = np.random.default_rng(7)
    noise = rng.integers(-32768, 32767, size=FS * 4).astype("<i2")
    with pytest.raises(apt.AptError, match="no APT sync"):
        apt.decode_samples(noise, FS)
    with pytest.raises(apt.AptError, match="shorter than one"):
        apt.decode_samples(np.zeros(FS // 5, dtype="<i2"), FS)
    with pytest.raises(apt.AptError, match="not a readable WAV"):
        apt.decode_wav(b"this is not a wav file at all")
