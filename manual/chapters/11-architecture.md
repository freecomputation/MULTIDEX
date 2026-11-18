# 11 Architecture

This chapter is for the contributor: the module map, the process model, and the data model at the level needed to build on them. The package is Python, small enough to read whole, with numpy the one library its signal-processing modules lean on. Every module carries a docstring stating its contract, and those contracts are the architecture; this chapter is their index.

## The module map

| Module | Contract |
| --- | --- |
| cli | The command surface: eight verbs, global options, exit codes |
| officer | The Watch Officer: bill building, source resolution, execution, interrupts, stand-down, resume |
| desks | The desk inventory (SPECS), the bench computation, every desk's process function |
| deskworker | The worker entry point running one desk behind the pipe contract |
| supervisor | One persistent worker subprocess per desk; restart once, then bench, reported |
| tuner | The Tuner Server and its sources; sole owner of the dongle |
| channelizer | Plans logical VFOs inside a dwell window; reports dropped channels |
| modes | Mode S frame work: CRC, typecodes, callsign, CPR position, altitude, velocity |
| codecs | Message codecs: ACARS, AIS, SAME, APRS, POCSAG batch |
| acars | The ACARS physical layer and ARINC 618 frame assembly, pure Python, behind the acars desk |
| apt | The package's own APT decoder: weather satellite pass audio to image, with plain-word failure reasons |
| cw | Morse detection and decode, pure Python, behind the amateur desk |
| sstv | SSTV decode, pure Python, behind the iss desk |
| audiocensus | The universal audio census engine: survey, extraction and voice-likelihood grading behind the audiocensus desk |
| index | The Index: event log, SQLite store, dossiers, aliases, snippet store |
| budget | The standing allocation ledger: pool, grants, reservations, reconciliation, the shedding account |
| products | Dissemination: FLASH, SITREP, Watch Report, coverage account, ask, status |
| gates | Muster gates and doctor text; the same checks with and without a watch |
| orbit | Orbital elements parsing, simplified propagator, element age rules |
| sim | The simulated source: deterministic synthetic spectrum from the seed |
| chime | The FLASH chime: sound, player detection, recorded outcome |
| console | The Console's frame builder: panel allotment and frame building from the view model and the display feed; pure, no terminal and no disk of its own |
| dashboard | The Console's view model: one snapshot of a watch, derived from the same records the products read |
| displayfeed | The display feed: the bounded ring files of capture spectra, desk work lines and officer thought lines the officer publishes in the watch folder for the Console |
| keepawake | The Console's keep-awake hold: the host's inhibitor program held as a child process under --keep-awake, declared in the dependence census |
| liveview | The Console's curses layer behind status --live: draws the frames the console module builds, holds the keep-awake hold, writes nothing |
| toolbind | The binding table for every external program the build invokes: presence, desk feeds, the dependence census |
| transcribe | Bounded transcription triage: the queue, its gates and budget, the faster-whisper engine binding |
| updater | The live update system: signed fetch, verification, staging, gates, activation and probation state |
| profile | Profiles: defaults, TOML loading, two-tier validation, time scale rule |
| bands | The band plan for the coverage account and anomaly rules; the quiet-band set |
| events | The normalized event: fields, kinds, grade enforcement at creation |
| exploit | Exploitation: assessments, first-contact and missing-regular checks, baselines, day picture |
| util | Shared machinery: formatting, JSON IO, ids, pid liveness, the product writer that refuses em dash characters |

One program stands outside the package: the keeper, in the repository's keeper folder, a standalone program with its own bootstrap that imports nothing from the package. It owns the generation pointer of chapter 14 and performs activation flips and rollbacks between watches. The separation is deliberate: the component that can replace the system must not depend on the system it replaces.

## The process model

Three kinds of process cooperate in a watch. The officer is one process, the one the watch verb starts, and it alone owns the bill, the Index writes and the watch folder. The Tuner Server is its capture arm: the sole code path that talks to the receiver, taking tuning orders and returning captures, which is what makes the sovereignty law enforceable, since there is exactly one place where a stand-down releases the hardware. On the real source the tuner shells to the host tools: rtl_sdr and rtl_fm for captures, Mode S and AIS demodulated in process, multimon-ng decoding the audio-borne formats, rtl_433 decoding the ISM population, and dumpvdl2, dsd-fme and dumphfdl feeding their desks, with jt9 decoding FT8 lines for the amateur desk. The binding table in the toolbind module is the whole census of those programs, and doctor prints it. A capture that reaches its time limit keeps and processes the bytes it got; a gap is filed only on zero usable bytes or an absent tool, and per desk, never as a silently lost dwell.

The desks are the third kind. The supervisor owns one persistent worker subprocess per standing desk, running the deskworker entry point. The contract is a pipe: a JSON job in, carrying the normalized feed and the context (watch id, desk, dwell times, tuned frequency, home position as a latitude and longitude pair, the profile, and the pass record where the item is a pass), and JSONL events out. A desk's process function is pure in the design sense: feed and context in, events out, no retuning, no file access, and no state that outlives the job except what a module keeps deliberately, such as the ADS-B desk's pending CPR frame pairs with their 10-second expiry.

The supervisor's failure discipline is fixed. A crash is a dead worker or a whole-job timeout of 600 seconds, generous on purpose: it exists to catch a hung desk, never to judge a slow one, and it applies to the whole job, never per output line, because workers write only when their process function returns. The first crash within an hour of watch time earns a restart and one retry. The second benches the desk for the rest of the watch. The benching is reported to the officer, which files it as a desk health event carrying the reason and the tail of the desk's stderr log, held at desk-stderr-DESKNAME.log in the watch folder, and the bench joins the one bench computation every surface reads. A contributor adding a desk inherits containment: a desk can fail as hard as it likes and the blast radius is its own benching and a gap in its own coverage line.

## The event and dossier data model

The event is the atom, defined and enforced in the events module: make_event refuses an unknown kind or grade at creation, so a malformed event cannot reach the Index by any path. The field set is the one chapter 08 lists. Two conventions carry the system's honesty. Desks state their limits inside their events where a limit shapes the product: a sonde carrier census says telemetry is not decoded on the event itself, a satellite pass whose recording carries no decodable APT says so on the event itself with the decoder's reason, a trunking census marks itself metadata only in its data. And evidence travels with claims: a snippet payload rides inside the event under a private key, the officer files it to the snippet store and replaces it with the stored path, and a snippet shed later is itself an event.

The Index stores events in the per-watch JSONL log and indexes them into SQLite. Dossiers are derived state: the index module resolves each event's emitter through the alias table to a canonical key, accumulates first and last hearing, watch membership and event count, and keeps the alias set. Watch metadata is stored per watch: start and planned end, source kind and substitution note, profile name, region, antenna, disk ceiling, ephemeris age, end state and resume state. The officer's state.json in the watch folder is a published view, not a store: pid, heartbeat in watch seconds, elapsed, current activity, counts, the benched list from the one computation, the next bill index and the complete flag. Status reads the view; the Index remains the truth.

Exploitation is the correlation layer over this model. It judges each batch of new events against the dossier state as it stood before the batch, so a first batch of two events from one emitter still counts as first contact. It raises assessment events with evidence links at the confidence the pattern supports, checks at SITREP boundaries and at stand-down for regulars gone missing, and at stand-down builds per-emitter baselines, the statistical ordinary against which future watches assess the new. A contributor extends the system by adding a desk, which produces events, or an exploitation rule, which produces assessments over events. The officer, the Index and the products need no changes for either, which is the test of whether an extension is shaped correctly.

## Extension points, stated once

A new desk is a SPECS entry with tier, service, CPU class and channels or the word "window", a process function and an entry in the PROCESS table, plus a consequence line in products for the coverage account when the desk benches. A new source is a class behind the Tuner Server interface with a kind, an availability check and a capabilities answer. A new product is a render function over the Index that carries a coverage account. Everything else in this manual is the behaviour those extension points produce when the shipped set is assembled.

