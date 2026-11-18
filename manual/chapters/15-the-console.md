# 15 The console

The dashboard of the early releases answered the operator's complaint that the system worked in silence with a status screen. The console, from 0.8.0, is the whole machine on glass: one fixed full-screen terminal frame in which the spectrum the tuner is hearing, the pipeline doing the work, the desks thinking, the officer deciding, the sky panel, and the standing pool are all visible at once, refreshed about once a second. It is opened with `multidex status --live`, and `multidex watch` on an interactive terminal hands the screen to it once the watch stands. Quitting it with q or Ctrl-C never affects the watch, and a completed watch renders its final account, marked complete.

The console is a viewer, and the discipline of the old dashboard holds whole. It reads what the officer publishes and writes nothing to the watch: no tuner handle, no Index writes, no ledger writes. It can be opened, closed, resized or killed at any moment and the watch neither knows nor cares. What it renders comes from two published sources, the watch's recorded state and Index, which the status verb also reads, and the display feed described below, which exists for the console alone.

## The frame

The screen is one fixed frame that redraws in place. Nothing ever scrolls. Every panel is allotted space from the live terminal size on every refresh, in a fixed priority order: the header, the waterfall, the pipeline, the sky panel, the pool and clock block, the event feed, and the mind stream. Each panel clips its own content hard to its allotment, newest first where the panel is a feed, and when space runs short the panels yield in the reverse of that order, the mind stream first and the waterfall last. A panel that cannot hold its minimum disappears cleanly instead of crushing. A live resize re-allots instantly, without restart. Below the console's honest minimum size the frame withdraws to the compact status rendering, the same account the plain status verb gives, and returns when the space does.

## The waterfall

Across the top runs the tuner's spectrum with its history dripping downward: the newest capture at the top, older captures descending and cooling as they fall, the axis labelled in frequency at both ends and at the centre. When a desk's filing falls inside a capture's span, the trace is bracketed in place and named with the emitter and its grade, and the bracket rides its trace down as the trace ages.

The waterfall's honesty rule is absolute, and it is the same rule the products live by: it shows captures, not a fiction of continuity. The tuner hears in dwells and sweeps, so the console renders each capture's spectrum as it arrives and holds the last true frame between captures, with the frame's age stated in the panel's title. A frame older than ninety seconds reads HELD, with its age. A satellite pass, whose feed is audio rather than spectrum, publishes no frame, and the waterfall holds through it in exactly this stated way. There is no interpolation, no smoothing invented for beauty, and no synthetic motion. The drip is the record's own pulse.

## The display feed

The officer publishes the feed as three small ring files inside the watch folder. The spectra file holds one frame per capture: a magnitude spectrum computed from samples the watch already took for its own work, the audio census desk's Welch spectrum over its window slice or the sweep scan's own power array, max-pooled to at most 240 bins, with the span it covers and the lock brackets the desks declared inside it. The work file holds each desk's real output as it happens, drawn from the events the desk itself produced, a few lines per desk per dwell. The thought file holds the officer's own filed record, muster verdicts, grants given and refused with their reasons, benchings, assessment changes, FLASH raisings and update handovers, one line per event at the moment it files.

The feed is bounded so it can never become a storage concern: each ring keeps a capped tail, forty spectra frames, two hundred and forty work lines, two hundred and forty thought lines, and the whole feed stays well under a megabyte per watch. It is not part of the watch's record, and the Index remains the record of truth. Publishing is best-effort by law: a feed failure is swallowed by the publisher and never reaches the watch. The profile's advanced key `display_feed` can turn the feed off; a watch with the feed off files an identical event record, and the console then renders from state and Index alone, with the waterfall and the streams standing empty and saying so.

## The pipeline and the mind stream

Beneath the waterfall, the pipeline shows the machine's anatomy as living blocks: the tuner, the channelizer, and every desk in the roster, each carrying its name, its state, and its present work. A desk at work shows its own output streaming inside its block, the callsigns the ADS-B desk is resolving, the frames the datalink desk is decoding, as they file. A benched desk shows its bench reason in its block, in the same plain words the status verb and doctor use, because all three read the one bench computation. An interrupt striking the watch is visible as it happens: the tuner block names the interrupt and the item it displaced.

Along the bottom runs the mind stream, the officer's thought as a continuous typeset record, rendered from the feed's thought lines. It is the truthful form of watching code run: not decorative characters, but the machine's actual decisions moving at the speed they occur. The event feed stands as its own panel beside it, graded catches newest first, and the two differ in kind: the feed is what the spectrum did, the stream is what the machine thought about it.

## The sky panel and the pool

The sky panel stands in the frame carrying the astronomical account's place. In this build its desks are not built yet, and the panel says so in the house style, the way a benched desk stands in a roster: it claims no sidereal time, no Jupiter window, no solar state and no meteor rate until the astronomical patch lands and its data arrives.

The pool block renders the standing allocation ledger as a reservoir: the standing cap, what is consumed, what is reserved, what is free, and the margin that is never granted, drawn as levels in a vessel, with this watch's own granted and held slice marked beside them. Grants and releases move the levels as they happen, because the block reads the same pool figures the officer's state file carries.

The header holds the watch's identity and posture: the watch id and its standing, the source stated as real or simulated in the same words every other surface uses, elapsed and remaining time, and, in a generation layout, the update system's posture: the current generation, its standing, and a probation in progress with its count. A rollback or a handover appears in the mind stream at the moment it files, and the header's generation follows the pointer at the next refresh.

## Colour, glyphs and keep-awake

Colour is data and nothing else. The three grades carry fixed colours in every panel, the waterfall brackets, the feed and the pipeline alike, and FLASH carries the one urgent treatment the frame owns, used nowhere else. The waterfall's intensity runs a single measured palette from the noise floor's dark to saturation's white heat, drawn with the terminal's block glyphs. A monochrome terminal degrades the whole frame to intensity alone without losing a single fact, because colour is never the only carrier of any distinction: the pool's levels are different glyphs before they are different shades, and a grade is a word before it is a colour.

Under the `--keep-awake` flag, on `status --live` or on `watch`, the console holds the display awake while it runs and releases the hold the moment it exits. There is no terminal-layer mechanism for this, so the hold is the host's own inhibitor program, held as a child process: systemd-inhibit on a systemd host, caffeinate on a Mac. Both stand in the dependence census the doctor prints, declared like every other binding. Where neither program exists, the header states plainly that the screen is not held, rather than pretending otherwise.

## Failure behaviour

The console fails the way an instrument should: visibly and without consequence. A stale feed shows its age in the waterfall's title. A watch whose state cannot be read yet shows that statement and fills when the state lands. A render error withdraws the frame to a plain statement and files its complaint to the home's console.log, off the watch's record. Nothing the console does, including dying, reaches the watch.
