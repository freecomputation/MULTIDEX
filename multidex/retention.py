"""Retention: evidence held in tiers under the standing pool.

Before 0.10.0 the snippet store shed by one rule - oldest
first, satellite material last, payload evidence never - a
fair rule that knew nothing about what the evidence was. The
research this build answers (the retention study: lossless
compression of raw IQ buys roughly 1.1 to 1.3 times and is not
worth building; practitioners keep features and excerpts, not
captures) gives the store its organizing law instead: evidence
is the smallest representation from which the event can be
re-decoded or re-classified, and what is held is organized by
how replaceable it is.

- Tier 0, permanent: decoded payloads and the vocoder frame
  archives (kind payload), and the event records themselves,
  which are the Index's JSONL log and are not snippets at all.
  Tier 0 never sheds; the ceiling yields to it, as it has since
  the payload exemption of 0.6.0, which this law generalizes.
- Tier 1, permanent products: the derived product of a
  capture - the per-dwell spectrum summary (kind spectrum),
  the decoded APT image and the decoded SSTV image. Small and
  bounded by construction. Tier 1 sheds only when no tier 2
  or tier 3 material remains to shed.
- Tier 2, selective excerpts: decimated channel excerpts
  around graded detections - the voice desks' audio snippets
  (kind voice, the transcription and grading input, filed as
  before) and channel IQ excerpts in SigMF (kind iq), filed
  for emissions the census extracted but could not classify,
  at or above the census's own extraction gate of 10 dB SNR,
  and for the sweep's strongest unknown emission at or above
  the 30 dB interrupt threshold. Tier 2 sheds by value.
- Tier 3, bulk capture material: the satellite pass audio -
  the whole recorded pass a decode ran from, the largest
  artifact one event files. Tier 3 sheds first; the decoded
  image it produced stands at tier 1 when it goes.

Sealed captures stand outside the tiers: a capture frozen
under the anomaly doctrine is never a shed candidate, is
filed even when nothing sheddable remains, and is accounted
apart in every reception account. Since 0.14.0 the anomaly
register seals the retained evidence of every cluster it
holds an entry for, so the class has a standing user.

Shedding inside a tier is value-aware, not oldest-first:
value rises with the emitter's uniqueness (an emitter heard
once is worth more than one heard a hundred times), with an
unresolved grade over a probable over a confirmed, and with
classifier uncertainty where a classification exists (a voice
score near the decision boundary, an unknown shape or an
unknown modulation). Oldest is only the tiebreak. Tier order
dominates value: the least valuable tier 3 file sheds before
the most valuable tier 2 excerpt, and any tier 2 excerpt
before any tier 1 product.

The arithmetic that motivates the law, stated as arithmetic
and not as a measurement: full-rate IQ at 1.024 Msps in cu8
runs about 2.05 MB per second, so a 20 GB pool holds about
2.7 hours of it; the tiered store's expectation is that the
same pool evidences on the order of a hundred times more
events. No watch has measured that ratio yet, and no surface
of this build claims it as a result.
"""

import os

# Snippet kinds, by tier. The set the desks file is closed;
# a kind not named here is treated as tier 3 bulk, and the
# tier test in the suite names every kind the desks file.
TIER_OF_KIND = {
    "payload": 0,
    "spectrum": 1,
    "image": 1,
    "voice": 2,
    "iq": 2,
}

TIER_NAMES = {
    0: "tier 0 (permanent evidence)",
    1: "tier 1 (derived products)",
    2: "tier 2 (selective excerpts)",
    3: "tier 3 (bulk capture material)",
}

# The one-paragraph policy doctor states.
POLICY = (
    "evidence is held in tiers under the standing pool. "
    "Tier 0 is permanent - decoded payloads and vocoder "
    "frame archives - and never sheds. Tier 1 is the derived "
    "product of a capture (per-dwell spectrum summaries and "
    "decoded images) and sheds last. Tier 2 is selective "
    "excerpts - voice audio and SigMF channel IQ excerpts "
    "around graded detections - and sheds by value. Tier 3 "
    "is bulk capture material (satellite pass audio) and "
    "sheds first. Sealed anomaly captures stand outside the "
    "tiers, never shed, and are accounted apart. Shedding "
    "inside a tier takes the least valuable evidence first: "
    "value rises with an emitter's uniqueness, with an "
    "unresolved grade, and with classifier uncertainty; "
    "oldest is only the tiebreak.")

_GRADE_WEIGHT = {"UNRESOLVED": 3.0, "PROBABLE": 2.0,
                 "CONFIRMED": 1.0}


def tier_of(kind, filename=None):
    """The retention tier of one snippet, from its kind and,
    for the satellite desk, its filename: the desk files its
    pass audio and its decoded image under the one kind, and
    the image is the derived product (tier 1) while the audio
    is the bulk capture (tier 3)."""
    if kind == "satellite":
        if filename and os.path.basename(filename).endswith(".png"):
            return 1
        return 3
    return TIER_OF_KIND.get(kind, 3)


def is_sigmf_part(filename):
    name = os.path.basename(filename or "")
    return name.endswith(".sigmf-data") or name.endswith(".sigmf-meta")


def sigmf_stem(filename):
    """The shared stem of a SigMF pair member, or None."""
    name = filename or ""
    for suffix in (".sigmf-data", ".sigmf-meta"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return None


def uncertainty_of(data):
    """Classifier uncertainty in [0, 1] from an event's data,
    where a classification exists: a voice score sits on a
    0-100 scale whose decision boundary is its middle; an
    unknown shape or an unknown modulation estimate is a
    classification the system could not make at all. Where no
    classification exists, the middle value."""
    if not isinstance(data, dict):
        return 0.5
    score = data.get("voice_score")
    if isinstance(score, (int, float)):
        return 1.0 - abs(float(score) - 50.0) / 50.0
    if data.get("unknown_shape"):
        return 1.0
    if data.get("modulation_estimate") == "UNKNOWN" \
            or data.get("modulation") == "UNKNOWN":
        return 1.0
    return 0.5


def value_of(event, dossier_events=None):
    """The retention value of the evidence an event carries.

    event is the Index event the snippet is filed under (or
    None, for a snippet no event names: value then reads from
    the neutral middle, so unlinked evidence is neither the
    first nor the last of its tier to go). dossier_events is
    the event count of the emitter's dossier: uniqueness is
    its reciprocal, so the only copy of an emitter outranks
    one of a hundred hearings."""
    if event is None:
        return 1.0
    grade_w = _GRADE_WEIGHT.get(event.get("grade"), 2.0)
    if event.get("emitter") and dossier_events:
        uniqueness = 1.0 / max(1, int(dossier_events))
    else:
        uniqueness = 1.0
    return grade_w * uniqueness * (0.5 + 0.5
                                   * uncertainty_of(event.get("data")))


def account(rows):
    """Aggregate snippet rows into the tier account every
    reception surface states: kept and shed per tier, and the
    sealed bytes apart. Rows are Index snippet rows (dicts
    with kind, path, bytes, shed, and optionally sealed)."""
    out = {tier: {"kept_count": 0, "kept_bytes": 0,
                  "shed_count": 0, "shed_bytes": 0}
           for tier in (0, 1, 2, 3)}
    sealed = {"count": 0, "bytes": 0}
    for row in rows:
        if row.get("sealed"):
            if not row.get("shed"):
                sealed["count"] += 1
                sealed["bytes"] += row["bytes"]
            continue
        tier = tier_of(row.get("kind"), row.get("path"))
        cell = out[tier]
        if row.get("shed"):
            cell["shed_count"] += 1
            cell["shed_bytes"] += row["bytes"]
        else:
            cell["kept_count"] += 1
            cell["kept_bytes"] += row["bytes"]
    out["sealed"] = sealed
    return out
