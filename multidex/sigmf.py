"""SigMF: the excerpt format, written and read in-house.

Tier 2 channel IQ excerpts are kept as SigMF pairs - a data
file of ci16_le samples beside a JSON metadata file carrying
the global, captures and annotations objects of the SigMF
core specification - so any future in-house decoder can
re-interpret an excerpt without guessing at its rate, its
frequency or its span. A headerless IQ file without metadata
is uninterpretable a year later; the pair is the evidence.

This module is deliberately minimal: it writes pairs and
builds the two officer payloads a desk attaches to an event.
It invokes no external program. Samples are complex values,
normalized so the excerpt's peak magnitude maps to the full
ci16 range; the peak is recorded in the metadata, so the
original scale is recoverable.
"""

import base64
import datetime
import json
import struct

DATATYPE = "ci16_le"


def _stamp(t_epoch):
    dt = datetime.datetime.fromtimestamp(
        float(t_epoch), datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + \
        "%03dZ" % (dt.microsecond // 1000)


def data_bytes(samples):
    """Complex samples -> (ci16_le bytes, peak). The peak is
    the magnitude the full ci16 range represents, so a reader
    recovers normalized samples as int16 / 32767 * peak."""
    vals = list(samples)
    peak = 0.0
    for v in vals:
        m = abs(v)
        if m > peak:
            peak = m
    # Coerced to a plain float: a numpy scalar peak (float32
    # from a complex64 capture) is not JSON-serializable and
    # would otherwise kill the metadata write downstream.
    peak = float(peak)
    if peak <= 0.0:
        peak = 1.0
    scale = 32767.0 / peak
    out = bytearray()
    for v in vals:
        i = int(round(v.real * scale))
        q = int(round(v.imag * scale))
        i = max(-32768, min(32767, i))
        q = max(-32768, min(32767, q))
        out += struct.pack("<hh", i, q)
    return bytes(out), peak


def meta_dict(samples_count, sample_rate, freq_hz, t_epoch,
              label=None, synthetic=False, description=None,
              peak=1.0, bw_hz=None):
    """The SigMF metadata object for one excerpt: one capture
    starting at sample 0 at the excerpt's centre frequency,
    one annotation spanning the whole excerpt over the
    channel's edges."""
    width = float(bw_hz) if bw_hz else float(sample_rate)
    annotation = {
        "core:sample_start": 0,
        "core:sample_count": int(samples_count),
        "core:freq_lower_edge": float(freq_hz) - width / 2.0,
        "core:freq_upper_edge": float(freq_hz) + width / 2.0,
    }
    if label:
        annotation["core:label"] = label
    return {
        "global": {
            "core:version": "1.0.0",
            "core:datatype": DATATYPE,
            "core:sample_rate": float(sample_rate),
            "core:num_channels": 1,
            "core:description": description or
                "MULTIDEX tier 2 channel IQ excerpt",
            "core:datetime": _stamp(t_epoch),
            "multidex:peak": float(peak),
            "multidex:synthetic": bool(synthetic),
        },
        "captures": [{
            "core:sample_start": 0,
            "core:frequency": float(freq_hz),
            "core:datetime": _stamp(t_epoch),
        }],
        "annotations": [annotation],
    }


def meta_bytes(samples_count, sample_rate, freq_hz, t_epoch, **kw):
    return json.dumps(meta_dict(samples_count, sample_rate,
                                freq_hz, t_epoch, **kw),
                      indent=1, sort_keys=True).encode("utf-8")


def pair_payloads_encoded(stem_filename, data_b64, samples_count,
                          sample_rate, freq_hz, t_epoch, **kw):
    """The two snippet payloads for an excerpt whose samples
    are already ci16-encoded (the census engine encodes at
    extraction, where the channel exists): the data file and
    its metadata, under the shared stem, kind iq. Filed in
    this order by the officer; the store sheds the pair
    together or not at all."""
    meta = meta_bytes(samples_count, sample_rate, freq_hz,
                      t_epoch, **kw)
    return [
        {"filename": stem_filename + ".sigmf-data", "kind": "iq",
         "b64": data_b64},
        {"filename": stem_filename + ".sigmf-meta", "kind": "iq",
         "b64": base64.b64encode(meta).decode("ascii")},
    ]


def pair_payloads(stem_filename, samples, sample_rate, freq_hz,
                  t_epoch, **kw):
    """The two snippet payloads a desk attaches to its event
    for one excerpt, from complex samples: the data file and
    its metadata, under the shared stem, kind iq."""
    vals = list(samples)
    data, peak = data_bytes(vals)
    return pair_payloads_encoded(
        stem_filename, base64.b64encode(data).decode("ascii"),
        len(vals), sample_rate, freq_hz, t_epoch, peak=peak, **kw)


def write_pair(stem_path, samples, sample_rate, freq_hz, t_epoch,
               **kw):
    """Write a SigMF pair to disk at stem_path (the .sigmf-data
    and .sigmf-meta files). Returns the sample count."""
    vals = list(samples)
    data, peak = data_bytes(vals)
    with open(stem_path + ".sigmf-data", "wb") as fh:
        fh.write(data)
    with open(stem_path + ".sigmf-meta", "wb") as fh:
        fh.write(meta_bytes(len(vals), sample_rate, freq_hz,
                            t_epoch, peak=peak, **kw))
    return len(vals)
