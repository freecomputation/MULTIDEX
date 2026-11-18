# MULTIDEX

An autonomous 24-hour signals watch on a single RTL-SDR. One command
stands the watch; the system plans its own watch bill, works it for
24 hours without supervision, correlates everything it hears into one
Index of emitter dossiers, and disseminates a small set of honest
products: FLASH alerts as warranted, a SITREP every six hours, and a
flagship Watch Report at stand-down with a full coverage account.

MULTIDEX receives only. It never transmits, never injects, and never
touches a network. The system core is the Python 3 standard library
(Python 3.11 or later); the signal-processing desks added in 0.2.0
(the audio census, ACARS, CW and SSTV) also use numpy, present on
the reference host, and file their absence as stated capture gaps
where it is missing. It runs on modest hardware.

Version 0.8.1.

## The verbs

The whole command surface is eight verbs. No verb prompts for input;
anything a verb needs comes from the profile or its arguments, and
anything it cannot get is a refusal with the reason in plain words.

- `multidex watch` - muster, then stand the 24-hour watch.
  `multidex watch --resume <watch-id>` resumes an interrupted watch
  from its on-disk state. On an interactive terminal the watch
  hands the screen to the Console (see `status --live`)
  once it stands; quitting the view leaves the watch running in
  the terminal, and the completion line prints when the watch
  stands down. Off a terminal, the watch runs as it always has,
  printing the completion line at stand-down.
- `multidex plan` - build and print the watch bill without touching
  the tuner.
- `multidex status` - five lines: current dwell, elapsed and
  remaining time, events and dossiers so far, FLASH count, desk
  health. A watch counts as running only while the officer process
  its state file records is still alive; a watch whose officer is
  gone is reported as not running and named as interrupted, and a
  watch folder with no state file is never reported as running.
  `multidex status --live` attaches the Console to the current
  watch: a full-screen live frame holding the spectrum waterfall,
  the pipeline of tuner, channelizer and desks with their work
  streaming inside them, the sky panel, the standing pool, the
  event feed and the officer's mind stream, refreshed about once
  a second from the watch's recorded state and the display feed
  it publishes (see The console, below). The view is read-only
  and quitting it (q or Ctrl-C) never affects the watch; a
  completed watch renders its final account, marked complete.
  Without an interactive terminal or curses, it prints the reason
  and the plain five-line status instead.
- `multidex sitrep` - render a SITREP for the running (or latest)
  watch now.
- `multidex stop` - stand the watch down at the next dwell boundary;
  the Watch Report covers the elapsed portion and says so. When no
  officer process is alive, stop says no watch is running and
  writes nothing.
- `multidex ask <query>` - query the Index: an emitter identity, a
  frequency such as `121.5 MHz`, a band such as `band uhf`, or a
  service such as `adsb`.
- `multidex update` - the live update system: `check` runs a
  fetch-verify-stage cycle now, `status` prints the update
  account, `approve` activates a staged candidate under the
  notify-only posture, `reset` clears the failed-activation
  record, and `rollback` rolls back to the previous generation
  (at the next safe boundary while a watch stands). See the
  live updates section below.
- `multidex doctor` - the muster checks and the full desk inventory
  with tiers, states, and bench reasons, and the dependence
  census: every external program this build invokes, the desk or
  function that uses it, and whether it is present on the host.
  Each entry is a stated dependence, retired only when an own
  implementation replaces it; the census is printed from the
  binding table itself, and the test suite asserts it names
  every program the package source invokes.

Run it from this tree with `./bin/multidex`, or as a module with
`python3 -m multidex`. Global options come before the verb:
`--home DIR` (default `$MULTIDEX_HOME` or `~/.multidex`) and
`--profile NAME` (a name resolved in the home's `profiles/` folder or
this tree's `profiles/` folder, or a path).

## How it works

Five organs, one tuner.

- The Watch Officer builds the watch bill and executes it. Class A
  clock events (satellite passes, radiosonde windows, scheduled
  alerts) own the tuner by the clock and are never preempted. Class B
  is a weighted standing rotation, re-weighted for day and night
  propagation. Class C census sweeps fill the gaps and own the
  deep-sweep hours. A strong unknown emission can interrupt B and C
  work, capped per hour. A home stands one watch at a time: a start
  that finds another watch's officer alive in the same home is
  refused by name, and the officer holds an exclusive lock on the
  home for its lifetime. The officer publishes its state at muster
  and at every dwell start and end, each save carrying its process
  id and a heartbeat, so elapsed time tracks the watch rather than
  jumping a dwell at a time. On the real source, dwell records and
  desk events carry the actual execution times from the watch clock
  (with the planned times recorded alongside), and pacing sleeps
  only the remainder of each item's planned duration, so the watch
  tracks wall time instead of overrunning by the sum of its capture
  times.
- The Tuner Server is the only code that talks to the receiver. Two
  sources sit behind it. The simulated source generates a
  deterministic synthetic spectrum and traffic load, so the whole
  system runs with no hardware attached. The real source shells to
  `rtl_sdr` and `rtl_fm` for captures and feeds the standing desks
  from them: Mode S is demodulated from the 1090 MHz capture in
  process, AIS is demodulated from discriminator audio in process,
  pager and APRS audio is decoded by `multimon-ng`, ISM devices are
  decoded by `rtl_433`, airband and weather radio run on real audio
  (weather radio SAME headers via `multimon-ng`), and the sonde,
  broadcast, cellular, and trunking desks are fed from the scan
  census. ACARS benches on the real source: it needs an
  acarsdec-class decoder, which is not among the bound host tools.
  A capture that reaches its time limit keeps and processes the
  bytes it did get; a gap is filed only when a desk's capture
  returns zero usable bytes or its tool is absent, and the loss is
  recorded per desk, never as a lost dwell.
- The Channelizer plans logical VFOs inside each dwell window, so
  several desks are served by one tuning. Channels outside the window
  are dropped and the drops are reported in the dwell record.
- The Desks are workers behind a pipe contract: a JSON job in,
  JSONL events out. Each desk runs in its own subprocess under a
  supervisor. A desk that crashes is restarted once; a second crash
  within an hour benches it, and the benching is filed as an event
  and carried into the products. The supervisor's timeout applies
  to the whole job (600 seconds), never per output line, so a slow
  desk is not mistaken for a crashed one; a worker that dies is a
  crash the moment it is seen. Each worker's stderr is captured to
  `desk-stderr-<desk>.log` in the watch folder, and a benching
  event carries the tail of that log.
- The Index is an append-only JSONL event log per watch plus a
  SQLite database rebuilt from the logs. Emitters accumulate
  dossiers; aliases (an aircraft's ICAO address, tail number, and
  flight callsign) resolve to one dossier across desks. A snippet
  store keeps bounded audio and imagery evidence under per-desk
  quotas and the profile's disk ceiling, shedding oldest first and
  logging every shed, with one exemption: payload-class evidence
  (the digital voice desk's vocoder frame archives) is permanent
  and is never shed. Snippet filenames carry the watch id and the
  bill item id, so two dwells can never produce the same path; a
  snippet refused by a cap or the ceiling files a refusal event
  with the reason. Since 0.4.2 the snippet store sits inside a
  standing allocation pool owned by the home, not by any
  watch: an allocation ledger lives beside the Index as
  durable data, read the same by every watch, by `doctor`, by
  status and by the dashboard, and never reset by a software
  update - a missing ledger reads as empty, and unknown fields
  in it are ignored. At bill build, every planned reception
  carries a computed allocation - tuner seconds and worst-case
  bytes from its capture rate and duration, satellite passes
  from their predicted AOS-to-LOS windows (an APT pass reserves
  its stored-audio rate of 22,050 bytes per second of window
  plus an image allowance; a METEOR pass is acknowledged only
  and carries a zero-byte allocation - no reservation, no
  grant). Satellite passes hold standing reservations in the ledger,
  computed from the ephemeris for the windows ahead whether or
  not a watch is standing, and a reservation whose window
  passes unclaimed expires and releases on its own. At muster
  the watch takes grants against the pool - the profile's disk
  ceiling held as a standing cap on the home, read against
  the home's actual disk state - adopting the reservations
  its passes match, sized from actual predicted windows and
  the pool's state at grant time, with a free margin of the
  pool never granted. If the worst case cannot fit, grants
  shrink in a stated order (event-allowance classes first,
  satellite passes last) and the reduction is stated in the
  muster events, rather than discovered mid-watch; an
  unplanned reception mid-watch takes its own grant the same
  way, shrunk or refused with the reason filed. During the
  watch, kept evidence is consumed against its grant, a
  capture that reaches its grant stops at the boundary with
  the truncation stated in its event, and a grant's unspent
  remainder releases back to the pool when its dwell or pass
  ends. Cancelling or stopping a watch releases all of its
  unspent grants at once, while its filed receptions stay
  accounted as consumed in the ledger's history; a resumed
  watch reconciles its grants against the ledger and the
  snippet store rather than assuming they survived, and files
  what changed. Shedding under the ceiling reads the whole
  home - the oldest filed receptions anywhere are the
  candidates, satellite imagery last - and every shed names
  the grant it was filed under. The Watch Report carries the
  reception account - this watch's slice of the standing
  ledger: granted against kept against released against shed,
  per class, with the payload-class evidence it holds and its
  shedding exemption stated - beside the coverage account, and `doctor` and
  status report the pool's own figures (cap, consumed,
  reserved, free) whether or not a watch stands. `budget.json`
  in a watch directory is a snapshot of the ledger at muster,
  never the system of record. Rebuilding the database
  from the logs preserves
  the snippet accounting, the event links, and the watch metadata,
  which the logs themselves do not record. Whether a desk is
  benched is computed once, from the capability, muster, and
  supervisor benches together, and every surface (officer state,
  status, doctor, muster events, the Watch Report) reads that one
  computation.

Exploitation correlates each new event against the Index and files
assessment events under five anomaly rules: new emitter, out-of-habit,
new shape in a quiet band, missing regular, and volume excursion.
Every claim carries one of three grades, enforced at write time:
CONFIRMED (decoded from the emission itself), PROBABLE (inferred from
a stated model), UNRESOLVED (detected, not characterized). Baselines
are provisional until three watches have contributed.

## Desk roster

Tier 1 desks are functional against both sources, decoding genuine
frames: against the simulator, frames the simulator emits at the
demodulated layer; against the real source, frames demodulated or
decoded from real captures as described above. ADS-B with real
Mode S decoding (CRC, callsign, CPR position pairs, velocity),
airband voice activity, ACARS (simulated source; benched on the
real source, above), AIS (types 1 and 5 with checksum), weather
radio with SAME alert decoding, ISM device bursts, APRS, and
POCSAG paging with real BCH error checking. Weather radio presence
from signal energy files as a PROBABLE census; CONFIRMED is
reserved for an actually decoded SAME header. Mode S position
pairs are only paired into a global position when the even and odd
frames arrive within ten seconds of each other, the CPR pairing
window; older frames expire unpaired. The ADS-B capture itself is
ten seconds at maximum tuner gain: Mode S is a weak-signal pulse
service, and at the moderate gain the other captures use, a rural
indoor antenna reads the band at the idle floor - field-checked
against live traffic, where maximum gain decoded airliners cleanly
in the same window the moderate gain heard nothing.

Tier 2 is implemented where pure Python honestly carries it and
benched with the reason where it does not. Satellite pass prediction
runs from TLE with a simplified two-body propagator and is graded
PROBABLE wherever it appears. On a lock, a NOAA pass is recorded
whole - the real source for the whole remaining pass at maximum
tuner gain, stored at 11025 Hz, the simulated source as a
synthetic recording shaped to the published APT line format -
and the recording is filed as the pass evidence. The satellite
desk decodes a NOAA pass recording into APT imagery with the
package's own APT decoder (multidex.apt): envelope recovery from
the 2400 Hz subcarrier, line framing on the APT sync patterns,
grey levels calibrated from the sync pulses themselves, and a
PNG written by the build's own encoder - no external program is
invoked. The image files with the pass event and is named in the
event and its FLASH; a recording that holds no decodable APT
states that its imagery was not decoded, with the decoder's
reason. METEOR satellites fly
an LRPT digital downlink, and are handled under an
acknowledgement policy: a METEOR pass is acknowledged only.
Its passes are still predicted and billed like any other, and
the pass event states that the pass was acknowledged and not
recorded under the policy; neither source attempts a capture
of any kind for a METEOR pass - no audio, no IQ - and no
standing reservation or grant is taken for one, its allocation
being zero bytes, stated as such in the event. A home whose
allocation ledger still holds METEOR reservations taken by an
earlier build releases them when the ledger is next loaded,
with the release recorded on each grant and stated by the
loading surface. NOAA APT passes are unaffected by the policy:
full reservation, whole-pass recording, and decode through the
package's own APT decoder. Radiosonde
windows are scheduled from
configured sites and synoptic hours; against the real source the
sonde desk files a presence census of carriers in the sonde band
and states that telemetry is not decoded, because an RS41-class
decoder is not bound. The amateur desk decodes CW in pure Python
and binds FT8 to the host's jt9 when present; the ISS desk decodes
SSTV in pure Python. Three desks bind decoder programs staged in
user space on the host - VDL2 to dumpvdl2, digital voice
signalling to DSD-FME, HFDL to dumphfdl - and stand when the
program is present, benching with the staging instruction as the
reason, stated in `doctor`, when it is not. HFDL coverage is
opportunistic: the rotation's day and night windows carry the
system-table channel sets, and the fixed antenna is a VHF/UHF
antenna. Transcription binds the host's faster-whisper stack the
same conditional way; where no engine is bound, voice audio is
filed as snippets and products report transcripts as not produced
rather than inventing them.

The digital voice desk files two products from a capture. The
first is the call signalling the bound program decodes - system,
talkgroup, source, slot, encryption flag - as metadata; voice
itself is not decoded, and no event claims otherwise. The second,
since 0.6.0, is the vocoder frame archive: the program is invoked
with its mbe data output enabled, and the de-interleaved vocoder
parameter frames it writes - the 49-bit AMBE+2 parameter frames
for DMR, P25 Phase 2 and NXDN, the IMBE frames for P25 Phase 1 -
are harvested after the run and archived as payload-class
evidence, linked to the desk event and the emitter dossier, with
their provenance (system, talkgroup or source where known, slot,
frame count, the capture window) recorded in the event. The
archive is small by construction: 49 parameter bits per 20 ms
frame is about 306 bytes per second of speech, 400 bytes per
second in the program's container with its per-frame error byte.
Payload-class evidence is permanent Tier-0 evidence, exempt from
shedding under the disk ceiling, and the reception account states
its bytes and the exemption. The frames are evidence, not audio:
AMBE+2 frames are archived undecodable until the in-house
synthesizer exists and the US8359197 gate opens on 2028-05-20,
and IMBE frames await the in-house IMBE build; each archiving
event says so. The program writes frames only for calls it can
hear in the clear (or with a keystream), so an encrypted call
archives no frames - the desk reports its signalling and states
the call was encrypted, and that is all.

Tier 3 desks are metadata-only by design: cellular and trunking
censuses count systems, bands, and activity and never attempt content.
The sweep desk is the anomaly sensor: every dwell files spectrum
observations with shape classification, and unknown shapes are graded
UNRESOLVED.

## Configuration

One profile file carries every knob: source kind and seed, home
position and antenna description, watch length, SITREP period, the
deep-sweep window, the disk ceiling, rotation weights and dwells, the
desk roster, FLASH triggers, the FLASH chime switch, snippet caps,
transcription bounds, radiosonde sites, and an advanced section
(gain policy, ephemeris file, interrupt cap, rehearsal outages, and
the time-scale test knob). The shipped `profiles/default.toml` is
commented throughout. A profile that fails validation is refused in
plain words before the watch stands. Validation refuses, among the
structural checks, a SITREP period of zero or over 24 hours (zero
would loop the cadence forever), a rotation entry that is not a
table or carries a bad weight or dwell, a radiosonde site without
numeric coordinates, a malformed excluded band, a non-numeric
FLASH watch frequency, an unknown desk or rotation name, and a
time scale that is not a positive number; a malformed
`MULTIDEX_TIME_SCALE` environment value refuses the start the same
way, never silently ignored. The effective time scale is recorded
in the watch metadata and the muster event.

The shipped default profile selects the real source. If the real
source cannot be stood at muster (the receiver tools are missing or
no dongle answers), the watch falls back to the simulated source
and states the substitution plainly in the muster output, in the
status verb output, and in every product's coverage account; it is
never silent. A profile that selects the simulated source
explicitly behaves exactly as it did before. The default profile's
home position is a reference position and must be set to the
watch's own position before a real watch.

The `[alerts]` section carries the FLASH chime switch, on by
default. When a FLASH is written and the chime is on, a two-tone
chime sounds through the first available of `pw-play`, `paplay`,
and `aplay` (the WAV is generated with the standard library at
first use); when no player is available the FLASH's event data
records that, and the chime never blocks or fails the FLASH.

Muster gates, checked before any watch: tuner present (with the
fallback above where the real source is selected), clock within one
second where an offset can be measured (the unverifiable case is
stated, not hidden), disk free space at least the profile ceiling,
profile valid, home position set. Satellite elements older than
seven days degrade the watch with a warning; older than thirty,
the satellite desk benches.

## Failure behavior

A crashed desk is restarted once, then benched, with the benching
filed. Tuner loss during a dwell becomes a gap event and is counted
in the coverage account; the watch continues on schedule. On the
real source a single desk's failed capture is filed as that desk's
gap and the dwell's other desks still report; a capture stopped at
its time limit is processed, not lost. Multi-channel desks capture
each channel in isolation: a dead airband or AIS channel records
its own error and the dwell keeps the other channels' evidence.
When `multimon-ng` is absent, weather radio presence is still
reported and the SAME limitation rides in the feed as a note; no
dwell files both a gap and a presence for the same desk. A capture
or desk-processing failure of any kind is isolated to its desk,
and an unexpected failure in one bill item files a system gap and
the watch continues to the next item; if the officer itself fails,
a stand-down (final state and Watch Report) is still attempted.
An interrupted watch resumes from its bill, state file, and event
log: the muster gates re-run, the bench set is re-filed, FLASH
numbering continues from the files on disk, dwells already
executed (known by their dwell records) are not flown twice, and
the resume event is stamped with the watch clock. The
`current_watch` pointer is a claim, not a fact: the status and
stop verbs judge liveness by the officer pid recorded in the
watch's state file, so a stop lands only in the folder of a watch
actually running, and a dead watch is reported as interrupted
rather than presented as live. Nothing fails silently: every loss
lands in the Index and in the Watch Report's coverage account and
desk health chapter.

## Live updates

A standing watch can take a new release without standing down.
An installation laid out as generations holds immutable release
trees under `releases/<commit-hash>/`, a clone used only to fetch
and verify, and a `current` link; a plain single-tree checkout
keeps working unchanged, and the machinery engages only where
the layout exists. The `keeper/` program, standalone and outside
the package, supervises the officer in a generation layout: it
holds the generation pointer in the home (atomic writes, a
checksum, a backup copy), starts officers, watches the heartbeat,
and performs handovers and rollbacks. It does no network, git,
verification, or device work, and the diff audit refuses any
release that touches it - the keeper is updated by hand, never
by a release.

A check fetches only, and identifies the candidate by a signed
annotated tag carrying a monotonic release counter. The tag
signature is verified with `git verify-tag` against an
allowed-signers file stored in the home, never in any repo; with
no signers file, verification fails closed in every posture.
The candidate must descend from the running commit, its counter
must exceed the highest ever recorded, a moved tag or a moved
remote stops the check, and the diff audit refuses binaries,
submodule changes, symlinks escaping the tree, unexpected file
modes, and keeper changes. A verified candidate is materialised
from its commit hash and re-verified file by file, then must
pass two gates: a full simulated watch under its own code, and
a read-proof against copies of the real home proving it arrives
at the same adoptable summary. A candidate that fails a gate is
marked bad and never retried. A candidate whose declared
writable state format exceeds the home's written format is
blocked in every posture: a format raise is a manual,
snapshot-first upgrade the operator performs with the watch
stood down, documented in the manual and deliberately not
automated.

Activation happens at a dwell boundary under the keeper, never
beside a satellite pass or inside the guard window before one.
The old officer snapshots the ledger and index, files the
handover, closes the tuner, and exits; the new officer adopts
the watch through the resume machinery and serves probation for
the first ordinary items. Probation failure restores the
snapshot, flips back, marks the generation bad, and files the
episode; three failed activations in 24 hours degrade the
automatic posture to notify-only until the operator resets it.
The profile's `[updates]` section carries the posture (the
shipped default is notify-only), the remote, the check interval,
the probation length, the pass guard window, and the freeze
warning days. Every stage files an event in the Index, status
and doctor carry an update account, and manual chapter 14
describes the mechanism, the escape hatch (stop the keeper, run
any retained generation's officer directly, restore the pointer
from its backup, restore state from a snapshot), and the honest
limits. One limit is a deployment gate: before the first live
handover on a new host, measure that the host's tuner closes and
reopens reliably across officer restarts, because the handover
depends on that reopening and the system cannot certify it from
inside.

## The console

The Console (0.8.0) is the system's terminal graphical face:
one fixed full-screen frame, redrawn in place about once a
second, that never scrolls. Its panels stand in a fixed
priority order - header, waterfall, pipeline, sky panel, pool,
event feed, mind stream - allotted from the live terminal size
on every refresh, each clipping hard to its allotment, yielding
in reverse priority order when space runs short, and withdrawn
to the compact status rendering below an honest minimum size.

The waterfall renders each capture's magnitude spectrum as it
arrives, newest at the top, history dripping down and cooling,
with lock brackets named in place where the desks' filings fall
inside the span. It shows captures, never a fiction of
continuity: between captures it holds the last true frame with
its age stated, and a frame older than ninety seconds reads
HELD. The pipeline shows the tuner, the channelizer and every
desk as blocks carrying their state and their real output as it
files; a benched desk shows its bench reason, and an interrupt
names the item it displaced. The mind stream is the officer's
own filed record rendered as it files: muster verdicts, grants
and refusals, benchings, FLASH raisings, update handovers. The
pool block draws the standing allocation ledger as a reservoir
with this watch's slice marked. The sky panel stands in the
frame with an honest benched statement until the astronomical
patch brings its data.

The officer publishes a bounded display feed for the Console
inside the watch folder: one spectrum frame per capture
(computed from samples the watch already took - the audio
census desk's Welch spectrum or the sweep scan's power array,
max-pooled to at most 240 bins), per-desk work lines, and the
officer's thought lines, each ring capped so the whole feed
stays well under a megabyte. The feed is not part of the
watch's record, publishing is best-effort and can never reach
the watch, and the profile's advanced `display_feed` key can
turn it off, in which case the watch files an identical event
record and the Console renders from state and Index alone. The
Console itself is a pure viewer: it writes nothing to the
watch, and quitting, resizing or killing it leaves the watch
exactly as it was.

With `--keep-awake` (on `status --live` or `watch`) the
Console holds the display awake while it runs and releases the
hold on exit. There is no terminal-layer mechanism for this,
so the hold is the host's inhibitor program - systemd-inhibit
or caffeinate - held as a child process; both stand in the
dependence census the doctor prints, and where neither exists
the header states plainly that the screen is not held.

## Testing

`python3 -m pytest` runs the suite: Mode S decoding against known
frames and encoder round trips, AIS/POCSAG/SAME/APRS/ACARS codecs,
orbit prediction, scheduler classes and placement rules, Index
dossier correlation and snippet shedding, anomaly rules, product
rendering (including the coverage account and a no-em-dash check),
muster gates, supervisor crash-restart-bench behavior, and an
end-to-end accelerated watch. The live update system is covered
against local git fixtures with real SSH-signed release tags:
bad signatures, moved tags, non-fast-forward candidates,
counter rollback, hostile diffs, both activation gates, the
format-raise block, officer handovers at and beside pass items,
pointer corruption recovery, and a keeper end-to-end probation
rollback. The Console is covered headlessly: recorded display
feeds are replayed through the frame builder at fixed terminal
sizes and the rendered frames asserted against the feed's
ground truth, and a simulated watch proves the display feed
changes nothing in the watch's event record. The real source is covered by unit
tests for its Mode S and AIS demodulators, by feed tests running
against shim receiver tools, and by a shimmed-real integration
test: fake `rtl_sdr`, `rtl_fm`, `rtl_power`, `rtl_433`, and
`multimon-ng` executables emit fixture bytes (genuine Mode S frames
with known ICAO addresses, a genuine AIS frame, an AM audio burst),
and a short real-source watch runs through the real CLI against
them, mustering on the real source with the previously benched
desks standing.

The watch engine accepts a time-scale factor (the profile's
`time_scale` knob, or the `MULTIDEX_TIME_SCALE` environment
variable), which accelerates pacing without changing any logic path.
A full 24-hour watch against the simulated source smoke-runs in
minutes this way, producing the same FLASH files, four SITREPs, and
Watch Report a real-time watch would.

## License

AGPL-3.0. See `LICENSE`.
