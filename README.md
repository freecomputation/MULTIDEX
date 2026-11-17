# MULTIDEX

An autonomous 24-hour signals watch on a single RTL-SDR. One command
stands the watch; the system plans its own watch bill, works it for
24 hours without supervision, correlates everything it hears into one
Index of emitter dossiers, and disseminates a small set of honest
products: FLASH alerts as warranted, a SITREP every six hours, and a
flagship Watch Report at stand-down with a full coverage account.

MULTIDEX receives only. It never transmits, never injects, and never
touches a network. It is built entirely on the Python 3 standard
library (Python 3.11 or later) and runs on modest hardware.

## The verbs

The whole command surface is seven verbs. No verb prompts for input;
anything a verb needs comes from the profile or its arguments, and
anything it cannot get is a refusal with the reason in plain words.

- `multidex watch` - muster, then stand the 24-hour watch.
  `multidex watch --resume <watch-id>` resumes an interrupted watch
  from its on-disk state.
- `multidex plan` - build and print the watch bill without touching
  the tuner.
- `multidex status` - five lines: current dwell, elapsed and
  remaining time, events and dossiers so far, FLASH count, desk
  health.
- `multidex sitrep` - render a SITREP for the running (or latest)
  watch now.
- `multidex stop` - stand the watch down at the next dwell boundary;
  the Watch Report covers the elapsed portion and says so.
- `multidex ask <query>` - query the Index: an emitter identity, a
  frequency such as `121.5 MHz`, a band such as `band uhf`, or a
  service such as `adsb`.
- `multidex doctor` - the muster checks and the full desk inventory
  with tiers, states, and bench reasons.

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
  work, capped per hour.
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
  and carried into the products.
- The Index is an append-only JSONL event log per watch plus a
  SQLite database rebuilt from the logs. Emitters accumulate
  dossiers; aliases (an aircraft's ICAO address, tail number, and
  flight callsign) resolve to one dossier across desks. A snippet
  store keeps bounded audio and imagery evidence under per-desk
  quotas and the profile's disk ceiling, shedding oldest first and
  logging every shed.

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
POCSAG paging with real BCH error checking.

Tier 2 is implemented where pure Python honestly carries it and
benched with the reason where it does not. Satellite pass prediction
runs from TLE with a simplified two-body propagator and is graded
PROBABLE wherever it appears; against the simulator, captured passes
file synthetic APT imagery as evidence, and against the real source
they file the captured pass audio, with APT imagery stated as not
decoded by this build. Radiosonde windows are scheduled from
configured sites and synoptic hours; against the real source the
sonde desk files a presence census of carriers in the sonde band
and states that telemetry is not decoded, because an RS41-class
decoder is not bound. Benched with reasons stated in `doctor`:
VDL2, HFDL, amateur SSB/CW/digital, DMR/P25/NXDN digital voice, and
ISS voice/SSTV decode - each needs a decoder program outside the
standard library. Transcription is likewise benched: no engine is
bound, voice audio is filed as snippets, and products report
transcripts as not produced rather than inventing them.

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
plain words before the watch stands.

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
its time limit is processed, not lost. An interrupted watch resumes
from its bill, state file, and event log. The `current_watch`
pointer is validated against the disk by the status and stop verbs:
a pointer naming a vanished or completed watch resolves to the
newest watch still running, so a stop always lands in the folder
of the watch actually running. Nothing fails silently: every loss
lands in the Index and in the Watch Report's coverage account and
desk health chapter.

## Testing

`python3 -m pytest` runs the suite: Mode S decoding against known
frames and encoder round trips, AIS/POCSAG/SAME/APRS/ACARS codecs,
orbit prediction, scheduler classes and placement rules, Index
dossier correlation and snippet shedding, anomaly rules, product
rendering (including the coverage account and a no-em-dash check),
muster gates, supervisor crash-restart-bench behavior, and an
end-to-end accelerated watch. The real source is covered by unit
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
