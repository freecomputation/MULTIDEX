"""The Channelizer: logical multi-VFO planning over one tuned window.

Given a window (center, bandwidth) and the desks that want to work in
it, the Channelizer cuts virtual receivers: one per desk channel that
falls inside the window. It enforces the window edge, a VFO cap, and
the design's CPU rule that at most two heavy desks work at once. What
it drops, it reports - a desk silently unserved would be a coverage
lie.
"""

MAX_VFOS = 12
MAX_HEAVY = 2


def plan(center_hz, bw_hz, desk_channels, heavy_desks=()):
    """desk_channels: {desk: [(freq_hz, bw_hz), ...]} plus optional
    per-desk whole-window entries marked with freq None.

    Returns (vfos, dropped) where each vfo is
    {desk, freq_hz, bw_hz} and dropped is a list of plain-word notes.
    """
    lo, hi = center_hz - bw_hz / 2, center_hz + bw_hz / 2
    vfos = []
    dropped = []
    heavy = 0
    for desk, channels in desk_channels.items():
        if desk in heavy_desks:
            if heavy >= MAX_HEAVY:
                dropped.append("%s dropped: heavy-desk limit" % desk)
                continue
            heavy += 1
        for freq, cbw in channels:
            if freq is None:
                vfos.append({"desk": desk, "freq_hz": float(center_hz),
                             "bw_hz": float(bw_hz)})
                continue
            if lo - 1 <= freq <= hi + 1:
                vfos.append({"desk": desk, "freq_hz": float(freq),
                             "bw_hz": float(cbw)})
            else:
                dropped.append("%s channel %d outside window"
                               % (desk, freq))
    if len(vfos) > MAX_VFOS:
        dropped.append("%d VFOs over the cap were dropped"
                       % (len(vfos) - MAX_VFOS))
        vfos = vfos[:MAX_VFOS]
    return vfos, dropped
