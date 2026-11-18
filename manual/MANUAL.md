# MULTIDEX

An autonomous 24-hour signals watch on a single RTL-SDR

Free Computation

Manual for MULTIDEX 0.1.3

# 01 Introduction

MULTIDEX is an autonomous signals watch system built on a single RTL-SDR receiver. The operator issues one small command and the system does the rest. It plans a 24-hour watch, schedules the one tuner across everything that tuner can reach, collects, decodes, classifies, correlates and disseminates finished products, all without further human direction. The operator may shape the watch in advance through a profile. Configuration is an option, never a requirement: the shipped defaults are complete and sufficient for a full watch on the first day the system is run.

The name expands as Multi-band Dissemination and Exploitation Index, and the expansion describes the work. The watch is multi-band because one tuner is spent across the whole spectrum it can reach, from HF to L-band. It disseminates because its output is finished products on a schedule, not a raw stream. It exploits because every capture is correlated against everything heard before it, so emitters accumulate dossiers and the unusual is assessed against the ordinary. And it is an index because the record it builds, the Index, is the durable product: a queryable account of what the spectrum around the home position contained, with the evidence filed beside each claim.

## The one-command premise

The premise is that a watch is a standing order, not a session. A conventional receiver setup asks the operator to be present: to choose frequencies, to notice activity, to decide what to record and what it meant. MULTIDEX moves all of that into the system. The operator states intent once, in a profile or in the defaults, and the watch runs for its full length on its own authority. If a question would need the operator mid-watch, the system answers it in advance from the profile, answers it from the defaults, or defers it to the products. No verb asks a question back, and no desk pages the operator at 03:00 to ask what a signal is. The signal is recorded, graded, filed, and reported in the next scheduled product.

## The design laws

Eight laws govern the system, and where a feature and a law conflict, the law wins and the feature is cut.

The first law is autonomy. A started watch runs to completion with no human input.

The second law is honesty. The system never invents content. A decode is reported at the confidence the evidence supports, an unidentified emission stays unidentified, and static is never dressed up as a voice. Every event in the Index carries one of three grades: CONFIRMED, where the evidence carries the claim outright; PROBABLE, a strong match with a named gap; and UNRESOLVED, recorded and honestly open.

The third law is coverage. Every product states what was not heard and why. One tuner cannot be everywhere, so every report carries its coverage account: where the tuner was, where it was not, and what classes of event the watch therefore cannot speak to. A product without a gap statement is a defect.

The fourth law is one tuner. The design never pretends the kit is more than it is. One RTL-SDR hears one window of spectrum at a time, and the whole art of the system is how it spends that window across 24 hours. Capabilities that need two tuners at once are out of scope, not faked.

The fifth law is sanity at the gate. A watch starts only when the system has verified its own footing: tuner present and free, clock true, disk headroom real, profile valid. If the footing fails, the system refuses with the reason in plain words and starts nothing.

The sixth law is locality. Everything stays on the operator's machines. There is no network dependence, no account, no telemetry, and no product leaves the host except by the operator's own hand.

The seventh law is sovereignty. The operator's stop is immediate and final, and the tuner is the operator's. If the operator takes the tuner, MULTIDEX stands down and records the gap. It never fights for the hardware.

The eighth law is receive only. There is no transmission path in the design, in any desk, at any tier. Encrypted traffic is metadata: presence, timing, volume and endpoints where broadcast. Its content is out of scope by construction, not by policy toggle.

## What MULTIDEX is not

MULTIDEX is not a live listening tool. It makes no sound during a watch, with one exception: a two-tone chime sounds on the host when a FLASH alert is written, if the profile's alerts section enables it, so an alert is audible in the room as well as filed. Tuning around by ear and listening to a conversation as it happens are a different program's work. MULTIDEX files audio evidence as snippets under bounded quotas and reports what it heard in its products; it does not present a speaker feed.

It is not a scanner toy. A scanner dwells where it is told and forgets everything by morning. MULTIDEX plans its day in advance as a written bill, accounts for every dwell and every gap, correlates each emitter into a dossier that persists across watches, and renders a flagship report whose coverage account can be audited against the dwell records in the Index.

It is also not a promise machine. Where a decode needs a program this build does not carry, the desk benches itself and states the reason in the doctor output, in the watch's own record and in the products. Chapter 07 states the current release's desk inventory exactly, including its benches, and chapter 13 describes the program that stands those desks up. Nothing in this manual describes a benched desk as if it were standing.

# 02 Getting started

## Hardware

The receiver is an RTL-SDR Blog V4 class USB dongle: a software defined radio that hands the host raw I/Q samples across a tuning range that covers HF through L-band, with a usable instantaneous bandwidth of about 2.4 MHz per tuning. That one window is the physical fact the whole design is built around. Chapter 01 states the law it produces: the watch is the art of spending one window well.

The antenna matters more than any software in this manual. The profile records which antenna is on the tuner at muster, because the coverage account is meaningless without it: a watch that heard nothing on a given band means one thing with a general coverage antenna and another with a discone or a long wire. The shipped default profile names a general coverage antenna. Set the profile's antenna line to what is actually connected before a real watch, so the record says what stood the watch.

The host is any machine that can stay up for the full watch length, hold the disk ceiling the profile sets, and run the host tools listed below. Sleep states and power saving that suspend the machine or the USB bus will tear gaps in the record, and the record will say so. The host clock must be true: passes, sonde windows and every timestamp are computed against it, and the muster clock gate fails a watch whose clock is more than one second off network time where the offset can be measured.

## Software requirements

MULTIDEX itself is Python, standard library only, and requires Python 3.11 or later. It is run from its repository tree; there is nothing to compile and no package index is consulted at run time.

The real source binds a small set of host tools, and the doctor verb checks for the footing they provide. The tools are the rtl-sdr suite, which supplies rtl_sdr for I/Q captures and rtl_fm for audio demodulation; rtl_433, which decodes the ISM device population; and multimon-ng, which decodes the audio-borne formats the build delegates to it, among them POCSAG paging, APRS packet audio and weather radio SAME headers. A machine without these tools can still run MULTIDEX on the simulated source, and chapter 09 describes exactly what that means and how the substitution is stated when it happens unasked.

## Installation

Clone the repository and run the launcher from the tree:

```
git clone https://github.com/freecomputation/MULTIDEX
cd MULTIDEX
./bin/multidex doctor
```

The launcher at bin/multidex runs the package in place. The equivalent module form is:

```
python3 -m multidex doctor
```

Global options come before the verb on every invocation. The two global options are --home, which names the data home, and --profile, which names the profile. The home is where everything the system writes lives: the Index, the watch folders, the profiles the operator keeps, and the current watch pointer. Its resolution order is fixed. The --home option wins when given. Otherwise the MULTIDEX_HOME environment variable is read. Otherwise the home is the .multidex folder in the operator's home directory. A typical explicit invocation looks like this:

```
./bin/multidex --home ~/multidex-home --profile default doctor
```

## Running doctor and reading its output

Doctor is the muster without the watch. It runs the same gates a watch must pass, then prints the full desk inventory. Run it after installation, after moving the dongle, after editing a profile, and whenever a watch refuses to start. Its exit code is 0 when no gate reports FAIL and 1 when any gate does.

The checks section prints one line per gate, each marked PASS or FAIL with the evidence in plain words. The gates are these:

| Gate | What it verifies |
| --- | --- |
| tuner | The source can be stood: the dongle and its tools on the real source, or the simulated source's readiness |
| clock | The host clock is plausible and, where chrony is present, within one second of network time |
| disk | Free space on the home volume meets the profile's disk ceiling |
| profile | The loaded profile validates with no problems |
| home position | The home position is set and is not the unset point at 0, 0 |
| ephemeris | Orbital elements are loaded and their age is stated; elements over 7 days old degrade pass scheduling to PROBABLE, and over 30 days old bench the satellite desk for the watch |

The ephemeris gate never fails a watch. It reports, and the benching it describes happens in the open: a watch stood on stale elements says in its own record that the satellite desk is benched and why.

Below the checks, doctor prints the desk inventory: every desk in the system with its tier, its service name, its state for this profile and source, and the reason for any bench. The last lines state the transcription state and the source capabilities. Chapter 07 is the full desk reference; the doctor inventory is the same truth computed for the machine in front of you, on the day you ask. When the two ever disagree, the doctor output is the one that describes your installation, and the disagreement itself is worth investigating before a watch is stood.

One doctor behavior deserves plain statement. If the profile asks for the real source and the real source cannot be stood, doctor does not present a bare tuner failure. It shows the footing the watch would actually fly on: the simulated source, with the substitution stated in the tuner line and the capability line. A watch stood in that condition carries the same statement in its muster output, its status and every product. The substitution is never silent. Chapter 09 covers the simulated source in full.

# 03 Your first watch

This chapter stands a first watch end to end on the shipped defaults with one change that is not optional: the home position. Everything else in the shipped profile is a complete, sane watch as it stands.

## Set the home position

The shipped default profile carries a reference position in the central United States, latitude 39.0, longitude -98.0, altitude 300 m, region "the central United States". Those coordinates are reference values only. Satellite passes, radiosonde windows and position reporting are all computed from this point, so a real watch flown on the reference position reports the wrong sky: it will hunt passes that are not overhead and miss the ones that are.

Copy the shipped profile under a new name in the home's profiles folder and edit the home section of the copy:

```
mkdir -p ~/.multidex/profiles
cp profiles/default.toml ~/.multidex/profiles/home.toml
```

In the copy, set lat and lon to the position of the antenna, alt_m to its altitude in metres, region to the region-level words the products may use for the position, and antenna to the antenna actually connected. The position itself stays local. Products describe it at region level only, and the muster gate checks that a position is set without printing it. Chapter 05 walks every key in the profile; the home section is the only one a first watch must touch.

Confirm the edited profile passes its gates before standing anything:

```
./bin/multidex --profile home doctor
```

## Run plan and read the bill

The plan verb builds the watch bill for the profile starting now and prints it without standing anything. It is the rehearsal of the watch: the same bill builder the officer will use, over the same gates, with no tuner taken.

```
./bin/multidex --profile home plan
```

The first line states the profile name, the start time, the item count and the class split. Each following line gives one bill item: start time, class and kind, label, centre frequency and the desks it carries. Read a first bill with three questions. Are the Class A items the ones the sky over the home position should produce: passes at plausible hours and sonde windows near the profile's synoptic hours? Does the Class B rotation cover the services you care about at a cadence you accept? Do the Class C sweeps reach the bands you expect, including the deep sweep hours? Chapter 06 explains the classes. If the bill is wrong, the watch will be wrong in the same way, and the time to fix it is now, in the profile.

## Stand the watch

```
./bin/multidex --profile home watch
```

The watch verb musters first. If any gate fails, the verb refuses with the failed gates named and their reasons, exits with code 2, and starts nothing. If the muster passes, the officer writes the bill into the new watch folder, records the desk benches for this watch in the open, publishes its first state, and begins executing the bill. The verb itself runs for the length of the watch: the officer is the process you started. It is normally stood in a terminal session or under a service manager that will keep it alive for the full watch length, and it holds an exclusive lock on the home for its lifetime. A second watch started in the same home while the first officer lives is refused by name, with the running watch and its officer process identified.

The watch id is formed from the start time in UTC, for example watch-20261118-000225. Everything the watch produces lives in the folder of that name under the home's watches folder.

## Read status

From any other terminal, the status verb reads the running watch's own published state:

```
./bin/multidex status
```

Status reports the watch id, elapsed and remaining time, what the officer is doing now, counts of events, dossiers and FLASH alerts, the benched desks if any, and the source flown, with the source note printed where the source was substituted. Status never guesses: a watch whose officer process is gone is reported as not running and named as interrupted, and a folder with no state file is never reported as running. Chapter 10 covers the interrupted case and the resume verb that answers it.

## Stop it early, and what a stand-down produces

```
./bin/multidex stop
```

The stop verb writes a stop flag into the running watch's folder. The officer sees it at the next dwell boundary, stands the watch down early, and renders its Watch Report for the elapsed portion, marked as ended early with the reason "operator stop". Stop is therefore not an abort that discards the day: four hours of a stopped watch produce the full product set for four hours, with a coverage account that covers exactly the elapsed span and says why it ends there. If no officer process is alive, stop writes nothing and says so; a dead watch cannot be stood down, only resumed or left as its record stands.

A completed or stood-down watch folder holds the bill, the watch metadata, the state file, the event log, the rendered Watch Report, any SITREPs and FLASH files, the filed snippets, and one stderr log per desk worker. Chapter 08 describes each product. The Index keeps the watch's events and dossiers after the folder's products are rendered, so the watch remains queryable with the ask verb for as long as the home is kept.

# 04 Command reference

The command surface is seven verbs and nothing else. No verb asks a question back. Anything a verb needs comes from the profile, the defaults or its arguments, and anything it cannot get is a refusal with the reason. Global options come before the verb: --home names the data home, resolved as --home first, then the MULTIDEX_HOME environment variable, then ~/.multidex; --profile names the profile, resolved as a path when the name is an existing file, then as a name in the home's profiles folder, then in the repository tree's profiles folder. The special profile name "default" with no file present loads the built-in defaults, which carry the same values as the shipped default.toml.

| Verb | Purpose |
| --- | --- |
| watch | Stand the watch (with --resume, resume an interrupted watch) |
| plan | Build and print the watch bill without standing anything |
| status | State of the running watch |
| sitrep | Render a SITREP for the current watch now |
| stop | Stand the running watch down at the next dwell boundary |
| ask | Query the Index |
| doctor | Muster checks and desk inventory without standing a watch |

## watch

Usage: `multidex [--home DIR] [--profile NAME] watch [--resume WATCH_ID]`

The watch verb loads the profile, resolves the source and runs the muster gates. On a gate failure it prints the refusal with each failed gate named, exits with code 2 and starts nothing. On a pass it creates the watch folder, writes the bill and the watch metadata, files the muster event and one desk health event per benched desk, publishes the first state, and executes the bill dwell by dwell for the profile's watch length. The process is the officer: it runs in the foreground for the whole watch and holds the home's watch lock throughout.

A start that finds another watch's officer alive in the same home is refused, with the running watch and its officer process named in the refusal. One home stands one watch at a time, and one tuner serves one watch.

With --resume, the verb takes the id of an interrupted watch. Resume re-runs the muster gates against the current footing, re-files the bench set, and continues the original bill from the first dwell that never filed its dwell record; recorded dwells are never re-flown, and FLASH numbering continues from the files on disk. A watch whose officer is still alive is not interrupted and cannot be resumed, and neither can a completed watch; the refusal says so. Chapter 06 describes the mechanics in full.

On normal completion or stand-down, the verb prints the watch id and the path of the watch folder holding the products, and exits with code 0.

## plan

Usage: `multidex [--home DIR] [--profile NAME] plan`

Plan loads the orbital elements, builds the bill for a watch starting now, and prints it. The header line gives the profile name, the start time, the total item count and the counts by class. Each item line gives the start time, class and kind, label, centre frequency and the desks carried, or "no desks" for an item flown by the census alone. Plan takes no tuner, writes nothing to the Index, and exits with code 0. It is the bill the watch verb would execute if stood at that moment, subject to the muster the watch verb additionally runs.

## status

Usage: `multidex [--home DIR] status`

Status reads the home's current watch pointer, validates it against the disk, and prints the running watch's published state: watch id, elapsed and remaining time, current activity, counts of events, dossiers and FLASH alerts, the benched desks or the statement that all standing desks are healthy, and the source kind. A watch on a substituted source carries its source note. When no watch is running, status says so in terms that match the disk: the last completed watch is named when the pointer names one, an interrupted watch is named as interrupted with the resume invocation given, and a folder with no state file is reported as a watch no officer is reporting for. Status exits with code 0 in all these cases; it is a report, not a gate.

## sitrep

Usage: `multidex [--home DIR] sitrep`

The sitrep verb renders a SITREP for the current watch now, outside the automatic cadence, and writes it into the watch folder with the next sequence number. It resolves the current watch allowing a completed one, so a SITREP can also be rendered over a finished watch's record. When no watch exists yet, it prints that there is nothing to report and exits with code 1. Chapter 08 describes the SITREP's contents.

## stop

Usage: `multidex [--home DIR] stop`

Stop determines which watch is actually running by liveness, not by the pointer alone: a watch is running only when the process id its state file recorded is still alive. For a live watch, stop writes the stop flag into that watch's folder and prints that the watch stands down at the next dwell boundary and renders its Watch Report for the elapsed portion. The verb then exits with code 0; the stand-down itself is the officer's work, performed when the flag is seen. When the pointer claims a watch whose officer is gone, stop prints that the watch stands interrupted and there is no officer process to stand down, writes no flag, and exits with code 1. When nothing is running at all, it prints that no watch is running and exits with code 1.

## ask

Usage: `multidex [--home DIR] ask QUERY [WORDS ...]`

Ask takes a query of one or more words, joined into a single query string, and answers from the Index. The query forms are an emitter key, a frequency, a band query and a desk name, with a dossier substring search as the fall-through. The output forms are described with worked examples in chapter 08. Ask reads only the Index in the home; it opens no tuner and writes nothing. It exits with code 0 whether or not the Index answers, and an unanswered query prints the forms it does answer to, so the operator can re-ask in a form the Index understands.

## doctor

Usage: `multidex [--home DIR] [--profile NAME] doctor`

Doctor runs the muster gates for the loaded profile without standing a watch, prints each gate's verdict and evidence, then prints the desk inventory: every desk with its tier, service, state for this profile and source, and the reason for any bench, followed by the transcription state line and the source capability line. Doctor exits with code 0 when no gate reports FAIL and code 1 when any does. A profile that fails to load is a refusal printed as such, with exit code 2, and this applies to every verb that loads a profile.

# 05 Profiles

The profile is the entire configuration surface. There are no other configuration files, no command-line tuning flags and no settings database. A profile is a TOML file. The shipped profiles/default.toml carries the same values as the built-in defaults in the profile module, so a missing or partial profile still yields a complete, sane watch: any omitted key takes the built-in value. A profile is loaded over the defaults and validated in two tiers. Hard problems, values that would crash, hang or corrupt a watch mid-run, refuse the load outright. Soft problems, such as a home latitude outside its range or an unset position, are reported as plain-word strings and also refuse the load. The muster profile gate reports the same results inside a watch start.

Copy the shipped file under a new name to shape a different watch. This chapter walks every section and key with its shipped default and what it changes.

## source

| Key | Default | What it changes |
| --- | --- | --- |
| kind | "real" | Spectrum source: "real" (RTL-SDR via rtl_sdr and rtl_fm) or "simulated" (deterministic synthetic source) |
| seed | 20261117 | Seed of the simulated world. Same seed, same spectrum |

The kind key's failure mode is stated, not hidden. A profile asking for the real source on a machine that cannot stand it, tools missing or dongle unreachable, produces a watch on the simulated source with the substitution stated in the muster output, the status verb and every product's coverage account. An explicitly simulated profile is used exactly as configured and carries no such note. Chapter 09 covers the simulated source in full.

## home

| Key | Default | What it changes |
| --- | --- | --- |
| lat | 39.0 | Home latitude; drives passes, sonde windows and local position reference |
| lon | -98.0 | Home longitude |
| alt_m | 300 | Home altitude in metres, used in pass prediction |
| region | "the central United States" | Region-level words products may use for the position |
| antenna | "general coverage antenna" | Antenna recorded at muster; the coverage account is read against it |

The shipped position is a reference position and must be replaced before a real watch, as chapter 03 directs. The position is local data: never printed by the gates, never published, and referred to in products only through the region words.

## watch

| Key | Default | What it changes |
| --- | --- | --- |
| hours | 24 | Watch length in hours; 24 is the standard the system is built around |
| sitrep_hours | 6 | SITREP cadence in hours of watch time |
| deep_sweep_start | 1 | Local hour the nightly deep census sweep window opens |
| deep_sweep_end | 4 | Local hour it closes |
| disk_ceiling_gb | 20 | Hard ceiling in GB on everything the watch writes, snippets included |

During the deep sweep window, 01:00 to 04:00 local as shipped, the bill's gap filling is given over to census sweeps of up to 900 seconds: the quietest bands in the quietest hours. Outside it, a census sweep is inserted at least every 45 minutes of filled time.

## rotation

Each Class B rotation entry has a weight, its share of the filled day, and a dwell length in seconds. The HF entries are additionally ruled by the clock: hf_day is eligible from 08:00 to 20:00 local and hf_night outside those hours, because propagation is not flat and neither is the bill.

| Entry | Weight | Dwell (s) | Centre | Desks |
| --- | --- | --- | --- | --- |
| adsb | 10 | 150 | 1090 MHz | adsb |
| airband | 9 | 600 | 125.0 MHz | airband |
| datalink | 6 | 300 | 131.55 MHz | acars |
| marine | 6 | 300 | 162.3 MHz | ais, wxradio |
| aprs | 4 | 240 | 144.39 MHz | aprs |
| ism433 | 5 | 240 | 433.92 MHz | ism |
| ism915 | 4 | 240 | 915 MHz | ism |
| pager | 4 | 300 | 929.6125 MHz | pager |
| cellular | 3 | 240 | 752.5 MHz | cellular |
| trunking | 3 | 240 | 851.75 MHz | trunking |
| fmband | 2 | 180 | 98 MHz | broadcast, sweep |
| hf_day | 4 | 300 | 14.15 MHz | sweep |
| hf_night | 4 | 300 | 7.1 MHz | sweep |

Every rotation dwell captures a 2.4 MHz window at its centre. An entry naming an unknown slot, a negative weight or a non-positive dwell is a hard profile problem. A weight of zero parks the entry without removing it.

## desks

| Key | Default | What it changes |
| --- | --- | --- |
| stand | the 14-desk list below | Desks that stand the watch |
| bench | empty list | Desks to bench even though they could stand |

The shipped stand list is: adsb, airband, acars, ais, wxradio, ism, aprs, pager, sweep, satellite, sonde, cellular, trunking and broadcast. A desk absent from the list is benched with the reason "not in the profile desk roster"; a desk in the bench list is benched with the reason "benched by the watch profile". Neither mechanism can stand a desk the build itself benches: the benched-with-reason desks of chapter 07 bench themselves regardless, and doctor shows all three kinds of bench with their distinct reasons. An unknown desk name in either list is a hard profile problem.

## alerts

| Key | Default | What it changes |
| --- | --- | --- |
| chime | true | Whether the two-tone FLASH chime sounds on the host |

The chime never blocks a FLASH. The file is written first, the chime sounded after, and the outcome, played or not played and why, is recorded in the triggering event's data. A host with no audio player records that the chime was not played, and the FLASH stands regardless.

## flash

| Key | Default | What it changes |
| --- | --- | --- |
| threshold_grade | "PROBABLE" | Lowest grade at which an assessment can raise a FLASH |
| watch_emitters | empty list | Emitter keys that always alert when heard |
| watch_freqs_hz | [121500000] | Frequencies that always alert when active, in Hz |
| guard_hz | 121500000 | Guard frequency the airband desk treats as an alert channel, 121.5 MHz |

The threshold grade must be CONFIRMED, PROBABLE or UNRESOLVED; anything else is a hard profile problem. Chapter 08 gives the full trigger set. The watch lists are the operator's standing additions to it.

## snippets

| Key | Default | What it changes |
| --- | --- | --- |
| iq_max_per_watch | 100 | I/Q snippets kept per watch, for unidentified or anomalous emissions, ten seconds at a time |
| voice_cap_per_desk | 200 | Voice transmissions kept whole, per voice desk, per watch |
| satellite_keep | true | Satellite imagery is product and is kept whole |

Snippet storage is bounded twice: by these caps per kind and desk, and by the disk ceiling across everything the watch writes. When a cap or the ceiling binds, the store sheds oldest first and files a system event for every shed, so the record of what was kept never silently diverges from what was heard.

## transcription

| Key | Default | What it changes |
| --- | --- | --- |
| desks | ["airband"] | Voice desks whose audio is transcribed in the background |
| cap_per_watch | 40 | Transcripts produced per watch, at most |

In the current release no transcription engine is bound, so this section configures a capability the build states as benched: voice audio is filed as snippets and transcripts are reported as not produced, in doctor and in the products. The keys are validated (a desk named here must be a known desk) and carried, so a profile written today means the same thing when an engine is bound. Transcription, when bound, never blocks collection and reports its backlog.

## excluded

| Key | Default | What it changes |
| --- | --- | --- |
| bands | empty list | Bands the watch never dwells on, as [low_hz, high_hz] pairs |

Any rotation entry or sweep window overlapping an excluded band is skipped by the bill builder. An entry that is not a numeric pair with low below high is a hard profile problem. Exclusion is the operator's tool for spectrum the watch must not spend time on: local regulation, a known interferer, or a band given to another instrument.

## sondes

| Key | Default | What it changes |
| --- | --- | --- |
| sites | two reference sites | Regional upper-air launch sites, each a name with lat and lon |
| synoptic_hours | [0, 12] | UTC synoptic hours the upper-air network launches against |

The shipped sites are a "North site" at 40.2, -98.3 and a "South site" at 37.8, -97.6, placed around the reference position. Balloons rise ahead of the synoptic hours and fly for about two hours. The bill builder turns each site within 700 km of home into Class A sonde windows: four sub-dwells of 600 seconds beginning 45 minutes before each synoptic hour. Replace the sites with your own region's launch sites, or the sonde desk will faithfully watch windows over somebody else's sky.

## advanced

| Key | Default | What it changes |
| --- | --- | --- |
| gain_db | { default = 23 } | Per-desk tuner gain in dB; a setting that serves airband starves HF, so each desk carries its own |
| tle_file | "tle.txt" | Orbital elements file, resolved beside the profile file |
| interrupt_cap_per_hour | 3 | Interrupts the officer may take per hour of watch time |
| time_scale | 1.0 | Watch-time seconds per real second; higher runs the identical logic at speed, for rehearsals. Also settable as MULTIDEX_TIME_SCALE, which wins |
| tuner_outages | empty list | Simulated tuner outages as [start_offset_s, duration_s] pairs, for rehearsing failure behaviour |

The time scale is guarded in the open: a malformed MULTIDEX_TIME_SCALE or a scale at or below zero refuses the watch start with the reason stated. A rehearsal knob the operator set is never dropped on the floor. Chapter 09 covers rehearsals and outages.

# 06 Anatomy of a watch

A watch has five movements: muster, bill, execution, stand-down, and, when fortune requires it, resume.

## Muster and its gates

Muster is the fifth law made concrete, and it takes under two minutes. First, mutual exclusion: a home stands one watch at a time, so a start that finds another watch's officer alive in the same home is refused by name before any gate runs, and the officer that does start holds an exclusive lock on the home for its lifetime. The lock is released by the kernel if the process dies, so a stale lock never blocks a new watch; the live process scan is the semantic gate.

The source is then resolved. A real profile on a machine that cannot stand the real source falls back to the simulated source, and the substitution note is carried into the muster record, the status output and every product. The gates then run, the same gates doctor prints: tuner, clock, disk, profile, home position and ephemeris, defined in chapter 02. If any gate fails, the watch does not start; the refusal names each failed gate with its detail, and nothing has been written except the refusal itself.

The ephemeris benches rather than fails. If no orbital elements are loaded, or they are more than 30 days old, the satellite desk is benched for the watch with that reason filed at muster. Elements 7 to 30 days old leave the desk standing with pass scheduling degraded and graded PROBABLE, stated in the products.

On a passed muster the officer assigns the watch id from the start time in UTC, creates the watch folder, builds the bill, writes bill.json and watch.json, sets the current watch pointer, starts the Tuner Server, and files the muster event recording the gate results, the bill length and the time scale. One desk health event per benched desk follows, each with its reason, so the bench set is part of the watch's own log from its first minute. The first published state follows immediately, before the first dwell, carrying the officer's process id and heartbeat.

## The bill and its classes

The bill is the written plan of the whole watch, built once at muster and executed in order. It has three classes, plus a fourth that only execution can create.

Class A items are clock events that own the tuner by the clock. Satellite passes come first: every pass of every loaded satellite over the home position inside the watch span, each starting two minutes before acquisition of signal and running two minutes past loss of signal. Radiosonde windows follow: for each launch site within 700 km, four 600-second sub-dwells beginning 45 minutes before each synoptic hour. Class A items are placed first; where two would overlap, the earlier placement stands.

Class B is the weighted standing rotation of chapter 05, filling the gaps between Class A items. The filler walks forward through each gap, laying either a Class C sweep under the rules below or a rotation entry picked by a credit scheme: every eligible entry accrues credit equal to its weight, the entry with the most credit is picked and pays the total, and the walk continues. Over a day, entries are served in proportion to their weights without a rigid repeating pattern. Eligibility respects the excluded bands and the HF day and night rules.

Class C census sweeps fill what the rotation does not take and own the deep sweep hours. A sweep cycles through twelve standing windows in order: 7.05, 14.2, 28.4, 50.5, 121.5, 146.5, 255.4, 401.5, 462.0, 751, 902.0 and 1290.0 MHz, each captured 2.4 MHz wide. In the deep sweep window, 01:00 to 04:00 local as shipped, every filled item is a sweep of up to 900 seconds. Outside it, a sweep of up to 300 seconds is inserted whenever 45 minutes have passed since the last one, and sweeps fill any gap where no rotation entry is eligible.

Class I items are interrupts, created only during execution. Every bill item carries an id in sequence, bill-001 onward, its class, kind, label, start, duration, centre frequency, bandwidth and desk list. The bill is a file in the watch folder, so the plan executed can be audited against the dwell records filed.

## The Watch Officer and the standing rotation

The Watch Officer is the process the watch verb starts, executing the bill dwell by dwell against the watch clock. On the real source the clock tracks wall time: an item that starts late because earlier work took real time executes at its true time and is recorded so, with planned times alongside, and pacing sleeps only the remainder of its planned duration. On the simulated source the bill's own times are the watch's times and execution is instantaneous in them.

For each item the officer determines which desks are standing under the one bench computation, asks the channelizer to plan logical VFOs for them inside the window, and orders one capture from the Tuner Server. Channels outside the window are dropped, and the drops are recorded in the dwell record. Each standing desk's feed runs through the supervisor (chapter 11), and the returned events pass through three officer duties in order: snippet adoption, filing snippet payloads to the store under their caps; FLASH evaluation, before persistence, so a chime outcome lands in the event's own data; and exploitation, where the correlation layer judges the batch. The officer then files the dwell record: bill id, class, label, standing desks, per-desk heard counts, window, dropped channels and planned start and end. Dwell records are the raw material of the coverage account. A dwell in which every desk heard nothing is recorded with the single word "silence", a finding, not an absence of one. State is published at every dwell start and end, so status tracks the watch rather than jumping a dwell at a time.

## Satellite pass scheduling

Passes are computed at muster from the loaded orbital elements with a simplified propagator, and predictions are graded PROBABLE throughout: satellite events carry the prediction grade beside the pass geometry, acquisition and loss times, maximum elevation and azimuths. Weather satellite passes fly with the satellite desk on a 150 kHz channel at the satellite's frequency: 137.1 MHz for NOAA 19, 137.62 MHz for NOAA 15, 137.9 MHz for METEOR-M2 4. ISS passes, downlink 145.8 MHz, fly with the sweep desk in this release because the ISS voice and SSTV desk is benched; the pass is in the bill and the window is captured and censused, and chapter 07 states the limit plainly.

## Interrupts

An interrupt is the one way execution writes its own bill. When a sweep observation reports an emission whose shape is unknown and whose signal stands 30 dB or more over the measured noise floor, the officer may break off after the current item and fly a closer look: a 240-second sweep dwell centred on the emission, at a bandwidth widened around the emission's own width. Interrupts preempt Class B and C work only, never Class A, and are capped by the profile at 3 per hour. The cap is a discipline, not a tuning detail: an officer that chased every strong unknown would never finish its rotation, and its coverage account would become a story about its enthusiasms.

## Gaps, and how they are recorded

A gap is a first-class event kind, filed whenever coverage the bill promised is not delivered. The causes are named in the event: tuner loss during a dwell, a capture failure for the whole dwell or for one desk or one channel within it, a desk processing failure, and an unexpected failure of a bill item, which is skipped so that one item's failure never ends the watch. A desk crash is a desk health event rather than a gap, with its own machinery in chapter 11. Every gap carries the bill id and the cause, so the Watch Report's coverage account is a join over dwell and gap records, not an estimate. The third law is enforced here, at the moment the gap happens.

## Stand-down and resume

A watch stands down in one of three ways: the bill runs out, the stop flag is seen at a dwell boundary, or the officer fails. Stand-down computes the exploitation close-out, including the missing-regulars check and the building of emitter baselines, renders the Watch Report, files the stand-down system event, publishes the final state with complete set true, and clears the current watch pointer. A watch ended early records the fact and the reason: "operator stop" or "officer failure". The final state is saved after the stand-down event is filed, so the counts in the state file equal the log exactly at completion, that event included.

Resume answers the interrupted case: the officer died, by host reboot, kill or crash, and the folder holds a partial record under a pointer claiming a watch nothing is running. Status names this state exactly, and `watch --resume WATCH_ID` continues it. Resume re-runs the muster gates on the current footing, restores the watch's own source from its metadata including a stated fallback, re-files the bench set, and continues the original bill, skipping every dwell that already filed its dwell record. A dwell interrupted mid-flight, which filed no record, is the only work re-flown. The resumed watch keeps its id, folder and FLASH numbering, and files a resume system event at the point in its own timeline where it stopped.

# 07 Desk reference

A desk is a decoder worker behind a pipe contract: it receives a normalized feed for its channels inside a dwell window and returns normalized events, and nothing else. It cannot retune the radio and cannot write outside its own output; snippet bytes travel back inside an event and the Watch Officer files them. Tier 1 desks decode identified services. Tier 2 desks work scheduled or windowed services, several dependent on external decoders. Tier 3 desks census populations and spectrum without decoding content.

A desk's state for a watch is computed once, from the build benches, muster benches, supervisor benches, source capabilities and profile roster together, and every surface reads that one computation: doctor, status, the muster events and the Watch Report cannot disagree about which desks are down. This chapter gives every desk, its channels or windows, what it produces and files, and its 0.1.3 status. The benches are stated in their sections and collected at the end.

| Desk | Tier | Channels or window | 0.1.3 status |
| --- | --- | --- | --- |
| adsb | 1 | 1090 MHz | standing |
| airband | 1 | six channels, 25 kHz each | standing |
| acars | 1 | 131.45, 131.55, 131.725 MHz | simulated source only |
| ais | 1 | 161.975, 162.025 MHz | standing |
| wxradio | 1 | 162.475 MHz | standing |
| ism | 1 | window (433 and 915 MHz) | standing |
| aprs | 1 | 144.39 MHz | standing |
| pager | 1 | 929.6125 MHz | standing |
| satellite | 2 | 137.1 MHz, 150 kHz | standing, ephemeris permitting |
| sonde | 2 | window (403 MHz) | standing, stated decode limit |
| broadcast | 2 | window (FM band) | standing |
| vdl2 | 2 | 136.975 MHz | benched |
| amateur | 2 | window | benched |
| digitalvoice | 2 | window | benched |
| hfdl | 2 | window | benched |
| iss | 2 | 145.8 MHz | benched |
| cellular | 3 | window | standing, metadata only |
| trunking | 3 | window | standing, metadata only |
| sweep | 3 | window | standing |

## adsb

Tier 1, light CPU, service ADS-B aircraft. The desk decodes Mode S frames in process on 1090 MHz, discarding frames that fail their CRC. Identification frames (typecodes 1 to 4) file decode events with the ICAO address and callsign, CONFIRMED. Position frames (typecodes 9 to 18) file telemetry with position and altitude: a position stitched from an even and odd frame pair within the 10-second CPR pairing window is CONFIRMED as a global decode; one decoded against the home position as local reference is PROBABLE, with the method named. Velocity frames (typecode 19) file telemetry with the velocity record. No snippets are filed.

## airband

Tier 1, moderate CPU, service airband voice. The desk works six channels of 25 kHz each: 118.700, 119.100, 121.500, 124.350, 126.075 and 127.800 MHz, inside the rotation window centred on 125.0 MHz. It files a voice event per detected segment with channel, signal to noise ratio and duration. A segment with a heard callsign files PROBABLE with the callsign as emitter; without one, UNRESOLVED. Guard frequency activity (121.5 MHz by guard_hz) files as an alert and is a standing FLASH trigger. Segments at 18 dB or better, and all guard segments, file their audio as a voice snippet under the caps; on the real source the snippet is the captured audio itself. The desk reports activity, not transcripts.

## acars

Tier 1, light CPU, service ACARS datalink. The desk decodes ACARS messages on 131.45, 131.55 and 131.725 MHz, filing decode events with the tail as emitter, the flight, the label and the message text, CONFIRMED. On the simulated source it stands. On the real source in 0.1.3 it is benched, the reason stated wherever the bench is reported: ACARS decode needs an acarsdec-class decoder program; it is not among the host tools this build binds, so on the real source the datalink dwell is flown by the sweep census instead. The window is still censused; the coverage account carries the consequence: no datalink messages are reported.

## ais

Tier 1, light CPU, service AIS vessels. The desk demodulates AIS in the marine window on 161.975 and 162.025 MHz. Position reports (types 1, 2 and 3) file telemetry with the MMSI as emitter, position, speed and course over ground, CONFIRMED. Static and voyage reports (type 5) file decode events with vessel name, callsign and destination; name and callsign become aliases of the vessel's dossier, so a ship heard by name and by number is one dossier. A site far from navigable water will hear little; that silence is geography, not failure, and the dwell records show the desk stood and listened.

## wxradio

Tier 1, light CPU, service weather radio and SAME. The desk works 162.475 MHz inside the marine window and keeps two grades sharply distinct. Presence, signal energy over threshold with no header decoded, files a census event at PROBABLE whose content states that the broadcast is present on signal energy and no SAME header was decoded this dwell. A decoded SAME header files an alert event at CONFIRMED with the alert type and areas as content, the parsed header in the data, and a FLASH raised. On the real source, SAME headers are decoded from the audio by multimon-ng. Presence says the transmitter is on the air; only a header says an alert is in force.

## ism

Tier 1, light CPU, window-fed, service ISM devices. The desk decodes the ISM population in the 433.92 and 915 MHz rotation windows, through rtl_433 on the real source. Each burst files a decode event, CONFIRMED, with the device as emitter and the model, decoded reading and signal strength in the data. This desk censuses the small radio population of a place: weather stations, tyre pressure sensors, remote controls and their kin, each a device dossier whose readings accumulate across watches.

## aprs

Tier 1, light CPU, service APRS packet. The desk decodes APRS packets on 144.39 MHz, the audio decoded by multimon-ng on the real source. Position packets file telemetry with the source callsign as emitter and the position in the data; other packets file decode events with the packet comment as content where present. Grades are CONFIRMED: a packet either parses or it does not file. The raw packet rides in the event data, so the decode can be re-examined against its source.

## pager

Tier 1, light CPU, service POCSAG paging. The desk decodes paging on 929.6125 MHz, decoding the batch structure itself on the batch path and accepting multimon-ng decodes from real audio at the same normalized layer. Each message files a decode event, CONFIRMED, with the emitter formed as PAGER: followed by the numeric address, the function code in the data and the message text as content. Events are marked broadcast in their data: a page is addressed, but radiated to everyone in earshot, and the Index records it on those terms. Chapter 12 addresses the handling of such content.

## satellite

Tier 2, heavy CPU, service weather satellite passes. The desk flies the Class A passes on a 150 kHz channel at the satellite frequency. Its events carry the prediction graded PROBABLE, with the pass geometry, beside the reception result. A pass with lock files a pass event at CONFIRMED with the measured signal quality. On the real source the evidence is the captured pass audio, filed as a satellite snippet, and the event states that APT imagery is not decoded by this build rather than filing an image it did not receive. A predicted pass with no lock files a pass event at UNRESOLVED with the failure recorded as "no lock": a miss is a record, and the SITREP's passes taken and missed come from these events. The desk benches at muster when no orbital elements are loaded or the elements are over 30 days old.

## sonde

Tier 2, light CPU, window-fed, service radiosondes. The desk works the Class A sonde windows at 403 MHz. On the simulated source it decodes telemetry frames, filing telemetry per frame with the serial as emitter, position and altitude, and a pass event when a flight completes at burst, a standing FLASH trigger. On the real source in 0.1.3 it censuses the window: a carrier present in the band files a census event at PROBABLE whose content states that telemetry is not decoded, because an RS41-class decoder is not bound in this build, with the carrier peak and noise floor in the data. The desk never invents a track; the limit is printed on the event itself.

## broadcast

Tier 2, light CPU, window-fed, service broadcast census. The desk censuses the FM broadcast band inside the fmband window. Each station found files a census event with frequency and peak power: CONFIRMED where an RDS identity was read, with the RDS name as emitter, and PROBABLE for a carrier without a read identity. Its product is a population record of the local broadcast band, against which change across watches is visible: a station that leaves the air or a new one that arrives changes the census, and the dossier layer holds the history.

## vdl2

Tier 2, light CPU, service VDL2 datalink, channel 136.975 MHz. Benched in 0.1.3: VDL2 decode needs a dumpvdl2-class decoder program, which this standard-library build does not bind. The desk has a specification, a channel and a tier, and no decoder behind the pipe in this release. It stands under no profile or source, and no product claims VDL2 traffic.

## amateur

Tier 2, moderate CPU, window-fed, service amateur SSB, CW and digital. Benched in 0.1.3: SSB, CW and FT8 decode need a WSJT-X-class decoder, outside standard-library scope. The bench note carries its mitigation: HF spectrum is still censused by the sweep desk on every HF dwell, so amateur activity appears as spectrum observations even though no amateur decode is produced.

## digitalvoice

Tier 2, heavy CPU, window-fed, service DMR, P25 and NXDN voice. Benched in 0.1.3: decode needs a DSD-class vocoder, which this build does not bind. The stated mitigation is that trunked systems are censused as metadata by the trunking desk. No digital voice content is decoded or claimed in this release.

## hfdl

Tier 2, light CPU, window-fed, service HF datalink. Benched in 0.1.3: HFDL decode needs a dumphfdl-class decoder program, which this build does not bind. HF spectrum continues to be censused by the sweep desk on the HF windows; HFDL messages are not decoded or claimed.

## iss

Tier 2, light CPU, service ISS voice and SSTV, channel 145.8 MHz. Benched in 0.1.3: ISS voice and SSTV decode need external decoders. The bench is partial in a visible way: ISS passes are still predicted by the ephemeris and appear in the bill, where they fly with the sweep desk, so the downlink window is captured and censused on every predicted pass even though no voice or image decode is produced.

## cellular

Tier 3, light CPU, window-fed, service cellular survey. The desk surveys the cellular window at 752.5 MHz and files census events per cell: band, cell identity as emitter, and reference signal received power, each marked metadata only in its data and content. It does not decode cellular content, and under the eighth law it never will: cellular traffic is encrypted traffic, and encrypted traffic is metadata by construction.

## trunking

Tier 3, light CPU, window-fed, service trunking census. The desk censuses the trunking window at 851.75 MHz and files census events per system, marked metadata only. On the simulated source a system files with its system id, site count and active talkgroup count, CONFIRMED. On the real source in 0.1.3 the desk works from the scan census: a persistent carrier files a census event at PROBABLE whose content states that it is consistent with a control channel, metadata only, and that system identity and talkgroup counts are not decoded by this build. The desk never files voice and never claims a talkgroup it did not decode.

## sweep

Tier 3, light CPU, window-fed, service spectrum census and anomaly. The sweep desk is the census engine of the watch, speaking for every window no other desk claims. It turns a window's spectrum into observations: one event per emission, with frequency, shape classification, bandwidth, peak power, the measured noise floor and the band name from the band plan. A shape that starts from "unknown" files UNRESOLVED; a classified shape files PROBABLE. A peak 30 dB or more over the floor marks the event an interrupt candidate, the input to the officer's interrupt machinery in chapter 06. The desk feeds the fmband, HF and Class C sweep items, substitutes for the benched ACARS desk on the real source's datalink dwell, flies the ISS pass windows and takes the interrupt dwells. Its observations accumulate into emission dossiers, so an unidentified shape returning nightly at one frequency becomes a known unknown with a history, the honest end-state for a signal nobody has decoded yet.

## The 0.1.3 bench list, collected

Stated plainly, as the release's limits: on the real source, acars is benched for want of an acarsdec-class decoder among the bound host tools. On every source, vdl2 is benched for want of a dumpvdl2-class decoder, hfdl for a dumphfdl-class decoder, amateur for a WSJT-X-class decoder, digitalvoice for a DSD-class vocoder, and iss for external voice and SSTV decoders. Transcription has no engine bound in this build: voice audio is filed as snippets and transcripts are reported as not produced. Two partial limits ride inside standing desks, stated on their own events: the satellite desk files pass audio and does not decode APT imagery, and the sonde desk on the real source censuses carriers without decoding telemetry. Chapter 13 describes the program that addresses each of these.

# 08 Products

A watch exists to produce its products: the Index and its dossiers, the event record, FLASH alerts, SITREPs, the Watch Report, the snippet store, and the ask surface that queries all of it. Every number in a rendered product comes from the Index at render time, and every product carries or points at its coverage account in its first lines. Products are plain Markdown, written to the watch folder, and they remain in the home after the watch ends.

## The Index and dossiers

The Index is the durable store, held in the data home. Its ground truth is an append-only JSONL event log per watch, events.jsonl in the watch folder; beside it sits a SQLite database in the home, rebuilt from the logs, which serves queries. Rebuilding preserves the snippet accounting, the event links and the watch metadata, which the logs themselves do not record, so the log remains the evidence and the database a faithful index over it.

Every event carries the same fields: id, watch id, desk, kind, start and end times, frequency where one applies, emitter where one is identified, confidence grade, content in plain words where the desk produced any, a desk-specific data record, the snippet path where evidence was filed, and links to related events. The kinds are decode, observation, voice, alert, pass, telemetry, census, dwell, gap, desk_health, assessment and system. Grades are enforced at the point of writing: an event with a grade outside CONFIRMED, PROBABLE and UNRESOLVED cannot be created, so it can never reach the Index.

Emitters accumulate dossiers. A dossier key is the emitter's canonical identity, and aliases resolve to it: an aircraft's ICAO address, tail number and flight callsign are one dossier across desks, and a vessel's name and callsign resolve to its MMSI dossier. A dossier records its kind, first and last hearing, the watches it appears in, its event count and its aliases. Dossiers persist across watches, which turns a sequence of 24-hour watches into a standing picture of a place: the regulars become known, and the exploitation layer can say, with the record behind it, that a regular is missing or a stranger is new.

## FLASH alerts and the chime

A FLASH is the watch's interrupt to its operator: a single file, FLASH-001.md onward in sequence, written the moment a standing trigger crosses. The triggers are fixed by the design and extended by the profile. An alert-kind event always raises one, covering SAME weather alerts and guard frequency activity. An exploitation assessment at or over the profile's threshold grade, PROBABLE as shipped, raises one. A completed pass the desks flag, such as a sonde flight reaching burst, raises one. A satellite pass captured with lock raises one. An event from an emitter on the watch_emitters list raises one. Voice or observation activity on a watch_freqs_hz frequency raises one, which is why the shipped profile lists the 121.5 MHz guard frequency there.

The FLASH file states what happened, where in frequency and band, when, the trigger, the confidence grade, and the ids of the events behind it. Sequence numbers are never reused, including across a resume: a resumed watch continues numbering from the files on disk.

When the profile's alerts chime is true, as shipped, a two-tone chime sounds on the host after the FLASH file is written. The chime is the watch's only sound. Its outcome is recorded in the triggering event's data, played or not played, so the record of an alert includes whether anyone in the room could have heard it announced. A FLASH is never blocked by the chime: with no audio player on the host, the FLASH records that it was not played and stands.

## SITREPs

A SITREP is the periodic situation report, rendered automatically every sitrep_hours of watch time, 6 hours as shipped, and on demand with the sitrep verb. Files are SITREP-1.md onward in the watch folder. A SITREP opens with its period and source line, then states the period's coverage in one paragraph: dwell time executed across how many dwells, and how many gaps, with bands not visited deferred to the Watch Report's account. It then gives the period's population, distinct emitters per desk; the notables, the period's alert, assessment and pass events up to twenty; the FLASH roll for the watch so far; desk health for the period, or the statement that all standing desks were healthy; and the day picture so far: events, passes taken and missed, alerts and assessments. At each automatic SITREP boundary the exploitation layer also runs its missing-regulars check, so an emitter that should have been heard and was not is assessed at the boundary rather than discovered at stand-down.

## The Watch Report and its coverage account

The Watch Report, REPORT.md in the watch folder, is the flagship product, rendered at stand-down over the whole watch or over the elapsed portion of an early stand-down, which it states. Its coverage account is the third law discharged: dwell time executed against dwell time planned, the gaps with their causes, the bands visited and never visited from the band plan, the desks benched with their reasons and the stated consequence of each bench for the picture the watch can present, and the source line, which names the source flown and carries the substitution note in full where the real source could not be stood. A reader who trusts nothing else in the report can audit the account against the dwell and gap records in events.jsonl, the same records the account was computed from. Beyond the account, the report presents the watch's population and assessments: dossiers heard, passes taken and missed, alerts raised, and the exploitation assessments with their evidence links.

## Snippets and their caps

Snippets are the filed evidence: voice audio from the voice desks, I/Q captures of unidentified or anomalous emissions, and satellite pass audio and imagery, kept in the watch folder's snippets folder. Every snippet filename carries the watch id and the bill item id, so two dwells can never produce the same path and one event's evidence can never overwrite another's.

Keeping is bounded by kind and by the whole. Voice snippets are capped per voice desk per watch at voice_cap_per_desk, 200 as shipped. I/Q snippets are capped per watch at iq_max_per_watch, 100 as shipped, held ten seconds at a time for unidentified or anomalous emissions. Satellite material is kept whole when satellite_keep is true. Across all kinds, the disk ceiling, 20 GB as shipped, binds the whole watch folder. When a cap or the ceiling binds, the store sheds the oldest material first and files a system event naming each shed path, so the evidence record degrades in the open and the products never claim evidence no longer on disk.

## Asking the Index

The ask verb answers four query forms, with a fall-through search. Worked examples follow; the outputs are the shapes the verb produces, over whatever Index the home actually holds.

An emitter key returns its dossier: kind, first and last hearing, watches and event count, aliases where there are several, and the ten most recent events.

```
multidex ask A9034C
```

A frequency returns the events near it, within a tolerance of half a percent or 12.5 kHz, whichever is larger, newest first. MHz, kHz and Hz units are all read, and a bare number is read as Hz.

```
multidex ask 121.5 MHz
```

A band query, the word band followed by a fragment of a band name from the band plan, returns the count of events in that band and the latest of them.

```
multidex ask band aviation
```

A desk name returns the service summary: the desk's tier, its total events in the Index, and its latest activity.

```
multidex ask adsb
```

Any other query falls through to a dossier substring search over dossier keys. A query nothing answers to prints the forms the Index does answer to, so a failed ask teaches the surface instead of merely failing.

# 09 The simulated source

The simulated source is a deterministic synthetic spectrum and traffic load that stands behind the same Tuner Server interface as the real source. Behind that interface the whole system runs unchanged: the officer builds the same bill, the desks process feeds through the same pipe contract, the Index accumulates the same event kinds, and the products render in the same forms. A watch on the simulated source is a real watch of a synthetic world.

## What it is for

The simulated source exists for three purposes. It proves the system end to end on a machine with no dongle, which is how the system is developed and how its test suite exercises full watches in seconds. It rehearses the operator: a first watch on the simulated source teaches the bill, the status rhythm and the products with no spectrum at stake. And it rehearses failure: the profile's advanced section carries simulated tuner outages precisely so the gap machinery, the stand-down paths and the operator's own procedures can be exercised against a tuner that fails on cue rather than by accident.

## Determinism and the seed

The simulated world is generated from the profile's source seed, 20261117 as shipped. Same seed, same spectrum: the same emitters, on the same schedules, with the same signal characters, in watch after watch. Determinism is the point. A regression that changes what a desk hears from the same seed is visible as a changed record, and a rehearsal can be re-run exactly. Change the seed and the world changes with it, while remaining internally consistent: the regulars of one seed are strangers to another, itself a useful property, because the exploitation layer's first-contact and missing-regular logic can be watched working against a population whose habits the operator has not memorised.

On the simulated source, the desks that are benched only on the real source stand: the ACARS desk decodes the simulated datalink traffic, and the satellite desk files decoded pass imagery as synthetic image snippets. The build benches of chapter 07 apply on both sources, because they are properties of the build, not of the spectrum: there is no VDL2 decode on the simulated source either, because there is no VDL2 decoder in the build at all.

## The stated-fallback rule

The fallback rule is the simulated source's most important behaviour, and it is a rule about speech, not about spectrum. The shipped default profile asks for the real source. On a machine where the real source cannot be stood at muster, because the host tools are missing or the dongle is unreachable, the watch does not refuse and does not silently pretend. It falls back to the simulated source, and the substitution is stated in four places that cannot be missed by anyone who reads the watch at all: the muster output printed at start, the source note carried in the watch metadata, the status verb's output for the whole life of the watch, and the coverage account of every product the watch renders. The note names the reason the real source could not be stood and states that every event in the watch is synthetic.

The rule cuts the other way as well. An explicitly simulated profile, source kind "simulated" set by the operator's own hand, is used exactly as configured and carries no substitution note, because nothing was substituted. And a watch resumed after an interruption flies the source recorded in its own metadata, fallback included, so a watch that mustered on the fallback does not quietly become a real watch at resume, or the reverse.

The design intent is that a synthetic record can never enter the Index disguised as a real one. The Index itself is source-agnostic; the watch metadata is not, and every surface that speaks for the watch speaks the metadata.

## Rehearsals with time_scale and tuner_outages

Two advanced profile keys exist for rehearsal. The time_scale key, watch-time seconds per real second, runs the identical logic paths at speed: a 24-hour watch at a scale of 3600 completes in 24 seconds of wall time with its bill, products and coverage account intact in watch time. The scale is fixed at muster and recorded in the watch metadata and the muster event. It can also be set through the MULTIDEX_TIME_SCALE environment variable, which wins over the profile. A malformed or non-positive scale refuses the start in the open, as chapter 05 describes.

The tuner_outages key lists simulated outages as [start_offset_s, duration_s] pairs in watch time. During an outage the source behaves as a lost tuner behaves, and the officer files the corresponding gap events. An empty list, as shipped, rehearses nothing. Together, the two keys let an operator answer questions that a real watch answers only once a day and at full price: what does the record look like when the tuner dies at hour nine, does the stand-down still render, does the resume path pick the bill up where the dwell records say it should. Chapter 10's failure entries are written to be rehearsed this way before they are met in earnest.

# 10 Troubleshooting

Failures in MULTIDEX are designed to be legible: a refusal in plain words, a gap event with a named cause, a bench with a stated reason. This chapter collects the failures an operator is most likely to meet, what the system says, and what to do. In every case the first diagnostic is the same pair of verbs: doctor for the footing, status for the watch.

## Doctor failures

A doctor run that prints FAIL on any gate exits with code 1 and names the gate with its evidence. Work the gates in order.

A tuner FAIL on a real profile means the source cannot be stood: the dongle is absent, held by another program, or its host tools are missing. Check the dongle is attached and no other receiver program is running, since one tuner serves one program at a time. Remember doctor's one softening: when the real source cannot be stood, doctor shows the simulated fallback footing the watch would fly on, stated in the tuner line, rather than a bare failure. A watch stood in that condition is a simulated watch, stated everywhere; if that is not the watch you meant to stand, the fix is the dongle or the tools, not the profile.

A clock FAIL means the host clock is more than one second off network time where chrony can measure it, or reads before 2024 at all. Synchronise the host clock and re-run doctor. A watch stood on a false clock writes false times into every record, which is why the gate is hard.

A disk FAIL means free space on the home volume is below the profile's disk ceiling. Free space, move the home, or lower the ceiling knowing it is also the watch's total writing budget. A profile FAIL lists the validation problems in plain words; chapter 05's hard problems refuse the load before doctor can run its gates, and the refusal text, naming the key and the defect, is the diagnostic. A home position problem means the position is unset or out of range; set lat and lon in the profile. The ephemeris line never FAILs; read it as a bench notice for the satellite desk, as chapter 02 describes.

A related refusal: the watch will not start because another watch is running in the same home. The refusal names the running watch and its officer pid. Status shows that watch and stop stands it down. A dead officer never produces this refusal, because the start path checks liveness, not folders.

## Tuner lost mid-watch

A tuner can be lost mid-watch to a USB reset, a cable, a hub power event or another program taking the device. The failed capture files a gap event naming the bill item and the cause "tuner loss", and the bill moves on: the watch does not end. When the tuner returns, later dwells capture normally, and the coverage account shows the hole exactly where it happened, bounded by the dwell records on either side. A watch that loses its tuner for hours is a watch with hours of stated gaps, the honest product of a bad night for the hardware. If the loss repeats, investigate the host's USB power management and any other program that believes the dongle is also its.

## A watch that will not stop

Stop works by a flag file the officer checks at dwell boundaries, so a stop issued during a long dwell takes effect at the end of that dwell, not inside it: a 900-second deep sweep dwell can delay a stand-down by minutes, and the stop verb says so in its output. If stop prints that no watch is running while a watch folder looks alive, believe the liveness rule behind it: the officer process whose pid the state file recorded is gone, so no process exists to receive the flag. That watch is interrupted, not running, and the answer is resume or nothing. If the officer is genuinely wedged, which the design works to make rare, killing it produces exactly the interrupted state: the record stands as filed, and `watch --resume` continues the bill from the first unrecorded dwell. Nothing is gained by deleting the folder.

## Stale watch pointers

The current watch pointer is a claim, not a fact, and the system treats it as one. Status, stop and sitrep resolve the pointer against the disk before acting: the folder must exist, its state must be readable, and for a watch to count as running the recorded officer pid must be alive. A pointer naming a completed watch resolves to the report that that watch completed. A pointer naming a vanished folder resolves to no watch. A folder with no state file is never reported as running. Stale pointers are therefore a cosmetic concern the system already absorbs; if status output surprises you, read it literally, because it describes the disk and the process table, not the pointer.

## Gaps in coverage

Gaps are not an error state; they are a record type. The Watch Report's coverage account lists them with causes, and each gap in events.jsonl names its bill item and cause. The causes to recognise are tuner loss, capture failure for a whole dwell, capture failure scoped to one desk or one channel, a desk processing failure, and an officer error that skipped one bill item. A single desk failing repeatedly inside otherwise healthy dwells points at that desk, and its stderr log in the watch folder, desk-stderr-DESKNAME.log, is the next read. A desk that crashes twice within an hour is benched by the supervisor for the rest of the watch; the bench appears in status, in a desk health event and in the report, with the tail of its stderr carried in the benching event. One gap pattern is self-inflicted: channels dropped by the channelizer, where a desk's channels fall outside a dwell's captured window, are recorded in the dwell record's dropped list. That is the bill and the desk set disagreeing about geometry, and chapter 07's channel tables against chapter 06's windows are where to reconcile it.

## Disk ceiling behaviour

The disk ceiling is a hard budget on everything the watch writes. As it binds, the snippet store sheds the oldest snippets first and files a system event for each shed, so a watch that outruns its storage produces a complete event record with a thinning evidence store, and says which evidence it shed. The ceiling does not stop the watch. If sheds begin early, the causes are a small ceiling for the site, a rich voice environment filling the per-desk voice caps, or satellite material kept whole under satellite_keep. The profile's snippet caps and the ceiling are the only knobs; chapter 05 gives their shipped values.

## Transcription backlog

In the 0.1.3 release this entry has a short answer. No transcription engine is bound, so no transcripts are produced and no backlog can form: voice audio is filed as snippets, and doctor, the status surfaces and the products report transcripts as not produced. A product that appears to quote speech is quoting a desk's decoded content, such as a pager message or an ACARS text on the simulated source, never a transcript. When an engine is bound in a future release, transcription is designed to run in the background under cap_per_watch, never blocking collection, and to report its backlog in the products rather than hiding it. Chapter 13 describes that program.

# 11 Architecture

This chapter is for the contributor: the module map, the process model, and the data model at the level needed to build on them. The package is Python, standard library only, and small enough to read whole. Every module carries a docstring stating its contract, and those contracts are the architecture; this chapter is their index.

## The module map

| Module | Contract |
| --- | --- |
| cli | The command surface: seven verbs, global options, exit codes |
| officer | The Watch Officer: bill building, source resolution, execution, interrupts, stand-down, resume |
| desks | The desk inventory (SPECS), the bench computation, every desk's process function |
| deskworker | The worker entry point running one desk behind the pipe contract |
| supervisor | One persistent worker subprocess per desk; restart once, then bench, reported |
| tuner | The Tuner Server and its sources; sole owner of the dongle |
| channelizer | Plans logical VFOs inside a dwell window; reports dropped channels |
| modes | Mode S frame work: CRC, typecodes, callsign, CPR position, altitude, velocity |
| codecs | Message codecs: ACARS, AIS, SAME, APRS, POCSAG batch |
| index | The Index: event log, SQLite store, dossiers, aliases, snippet store |
| products | Dissemination: FLASH, SITREP, Watch Report, coverage account, ask, status |
| gates | Muster gates and doctor text; the same checks with and without a watch |
| orbit | Orbital elements parsing, simplified propagator, element age rules |
| sim | The simulated source: deterministic synthetic spectrum from the seed |
| chime | The FLASH chime: sound, player detection, recorded outcome |
| profile | Profiles: defaults, TOML loading, two-tier validation, time scale rule |
| bands | The band plan for the coverage account and anomaly rules; the quiet-band set |
| events | The normalized event: fields, kinds, grade enforcement at creation |
| exploit | Exploitation: assessments, first-contact and missing-regular checks, baselines, day picture |
| util | Shared machinery: formatting, JSON IO, ids, pid liveness, the product writer that refuses em dash characters |

## The process model

Three kinds of process cooperate in a watch. The officer is one process, the one the watch verb starts, and it alone owns the bill, the Index writes and the watch folder. The Tuner Server is its capture arm: the sole code path that talks to the receiver, taking tuning orders and returning captures, which is what makes the sovereignty law enforceable, since there is exactly one place where a stand-down releases the hardware. On the real source the tuner shells to the host tools: rtl_sdr and rtl_fm for captures, Mode S and AIS demodulated in process, multimon-ng decoding the audio-borne formats, rtl_433 decoding the ISM population. A capture that reaches its time limit keeps and processes the bytes it got; a gap is filed only on zero usable bytes or an absent tool, and per desk, never as a silently lost dwell.

The desks are the third kind. The supervisor owns one persistent worker subprocess per standing desk, running the deskworker entry point. The contract is a pipe: a JSON job in, carrying the normalized feed and the context (watch id, desk, dwell times, tuned frequency, home position as a latitude and longitude pair, the profile, and the pass record where the item is a pass), and JSONL events out. A desk's process function is pure in the design sense: feed and context in, events out, no retuning, no file access, and no state that outlives the job except what a module keeps deliberately, such as the ADS-B desk's pending CPR frame pairs with their 10-second expiry.

The supervisor's failure discipline is fixed. A crash is a dead worker or a whole-job timeout of 600 seconds, generous on purpose: it exists to catch a hung desk, never to judge a slow one, and it applies to the whole job, never per output line, because workers write only when their process function returns. The first crash within an hour of watch time earns a restart and one retry. The second benches the desk for the rest of the watch. The benching is reported to the officer, which files it as a desk health event carrying the reason and the tail of the desk's stderr log, held at desk-stderr-DESKNAME.log in the watch folder, and the bench joins the one bench computation every surface reads. A contributor adding a desk inherits containment: a desk can fail as hard as it likes and the blast radius is its own benching and a gap in its own coverage line.

## The event and dossier data model

The event is the atom, defined and enforced in the events module: make_event refuses an unknown kind or grade at creation, so a malformed event cannot reach the Index by any path. The field set is the one chapter 08 lists. Two conventions carry the system's honesty. Desks state their limits inside their events where a limit shapes the product: a sonde carrier census says telemetry is not decoded on the event itself, a satellite pass on the real source says imagery is not decoded on the event itself, a trunking census marks itself metadata only in its data. And evidence travels with claims: a snippet payload rides inside the event under a private key, the officer files it to the snippet store and replaces it with the stored path, and a snippet shed later is itself an event.

The Index stores events in the per-watch JSONL log and indexes them into SQLite. Dossiers are derived state: the index module resolves each event's emitter through the alias table to a canonical key, accumulates first and last hearing, watch membership and event count, and keeps the alias set. Watch metadata is stored per watch: start and planned end, source kind and substitution note, profile name, region, antenna, disk ceiling, ephemeris age, end state and resume state. The officer's state.json in the watch folder is a published view, not a store: pid, heartbeat in watch seconds, elapsed, current activity, counts, the benched list from the one computation, the next bill index and the complete flag. Status reads the view; the Index remains the truth.

Exploitation is the correlation layer over this model. It judges each batch of new events against the dossier state as it stood before the batch, so a first batch of two events from one emitter still counts as first contact. It raises assessment events with evidence links at the confidence the pattern supports, checks at SITREP boundaries and at stand-down for regulars gone missing, and at stand-down builds per-emitter baselines, the statistical ordinary against which future watches assess the new. A contributor extends the system by adding a desk, which produces events, or an exploitation rule, which produces assessments over events. The officer, the Index and the products need no changes for either, which is the test of whether an extension is shaped correctly.

## Extension points, stated once

A new desk is a SPECS entry with tier, service, CPU class and channels or the word "window", a process function and an entry in the PROCESS table, plus a consequence line in products for the coverage account when the desk benches. A new source is a class behind the Tuner Server interface with a kind, an availability check and a capabilities answer. A new product is a render function over the Index that carries a coverage account. Everything else in this manual is the behaviour those extension points produce when the shipped set is assembled.

# 12 Operation, law and ethics

## Receive only, by construction

MULTIDEX cannot transmit. There is no transmission path in any desk, at any tier, in either source: the Tuner Server takes tuning orders and returns samples, and no code path in the system keys a transmitter, because the hardware it drives is a receiver and the design treats that as a law rather than a limitation. The eighth law's second half follows from the same construction. Encrypted traffic is metadata: the cellular and trunking desks census presence, band, identity where broadcast and signal strength, and mark every event metadata only. Content that the watch cannot lawfully or technically read is not approached; the census is the product.

This matters practically as well as ethically. An operator asked what the system does to the spectrum can answer exactly: nothing. It listens, it records what it heard, and it accounts for where it was not listening. Interference claims, direction-finding disputes and most regulatory theories about active equipment do not survive contact with a receive-only record that includes its own gaps.

## The operator's responsibility for local law

Reception law varies by jurisdiction and by service, and this manual does not give legal advice. The operator is responsible for knowing and following the law where the antenna stands: which bands may be received, which received content may be recorded, retained or disclosed, and which services carry statutory protection even against reception. The system gives the operator the tools to comply. The profile's excluded bands keep the watch off spectrum entirely. The desk roster benches whole services. The snippet caps bound how much intercepted audio is retained, and the disk ceiling bounds the whole record. Where a jurisdiction restricts a service, the compliant configuration is to bench the desk or exclude the band, and to say so in the profile that stands the watch, so the record itself shows the discipline was applied in advance rather than improvised afterward.

Nothing in the system publishes. Under the locality law there are no accounts, no uploads and no telemetry; a product leaves the host only by the operator's own hand. That design choice keeps the disclosure decision where the law puts it: with the operator, per product, with the coverage account attached.

## Privacy discipline for intercepted audio

A watch hears other people's transmissions. Some of what it files is broadcast by design, weather radio, aviation voice on published frequencies, satellite downlinks meant for anyone with a receiver. Some of it is addressed traffic radiated in the clear, pager messages being the standing example in this build: addressed to one recipient, audible to everyone in earshot, and recorded by the pager desk on exactly those terms, marked broadcast in the event data because the radiation, not the addressing, is what the receiver met.

The discipline the system applies is minimisation and plain labelling. Voice audio is kept as snippets under per-desk caps, not as a rolling archive of everything audible. Transcripts are not produced by this build at all. Dossiers are built on emitter identities that are themselves broadcast, callsigns, addresses and identifiers transmitted for the purpose of being received. The Index is local data on the operator's machine, and the operator who shares a product shares a document they have read, with the coverage account in it, rather than a feed they have not. Operators should apply the same minimisation to their own handling: keep watches for the spectrum questions they were stood to answer, shed evidence the question does not need, and treat any intercepted content that identifies a private person with the care its sensitivity deserves regardless of how it arrived.

## Position discipline in products

The home position is the most sensitive fact a watch holds, because the whole record is indexed to it. The system's discipline is structural. The position lives in the profile, which is local. The muster gate verifies that a position is set without printing it. Products refer to the position only through the profile's region words, "the central United States" in the shipped default, so a Watch Report can be shared without sharing coordinates. Ask answers speak about emitters, frequencies, bands and desks, not about the receiver's location. The shipped reference position is a further guard for new operators: a first watch stood without editing the profile reports the sky over the reference position, wrong but harmless, rather than the sky over a real antenna whose coordinates were never meant to travel. Set the position for a real watch, keep the profile with the machine, and let the products carry the region words. That is the whole of the discipline, and it is enough when it is followed.

# 13 The 0.2.0 program and license

This chapter is clearly marked as forthcoming. It describes the 0.2.0 program of work: what is in development, what each item is intended to do, and the terms on which it will be reported when it lands. Nothing in this chapter is shipped in 0.1.3. Where this chapter and an earlier chapter differ, the earlier chapter describes the current release and this chapter describes intent. The desks named here bench in 0.1.3 with the reasons chapter 07 states, and they will be described as standing only when a release stands them and its doctor output says so.

## The desks being stood up

The largest part of the program is the bench list. Each benched desk is being given the decoder its bench reason names, bound into the build on the same pipe contract as every other desk, so that standing it changes its doctor line and its coverage consequence and nothing else about the system's shape.

ACARS on the real source is the nearest item. The desk already decodes on the simulated source; the work is an acarsdec-class decode path for real captures, so the datalink dwell reports messages on the real source instead of being flown by the sweep census. VDL2 and HFDL follow the same pattern, with dumpvdl2-class and dumphfdl-class decode paths. The amateur desk is being given SSB audio capture and CW and digital decode through a WSJT-X-class path, converting the HF presence the sweep desk already reports into identified amateur traffic. The ISS desk is being given voice capture on 145.8 MHz and SSTV image decode, so ISS passes already in the bill produce a voice and image record instead of a censused window alone.

Digital voice is the item whose terms are stated most carefully, because the honesty law requires it. Where a DMR, P25 or NXDN decode path can be carried, the desk is being stood up first as signaling and metadata: call events, talkgroup and radio identifiers, system structure, the facts a receiver can establish from the signal itself. Intelligible voice requires a vocoder for the AMBE family of codecs, and where a release does not carry one, the desk will report signaling and metadata and say that voice is not decoded, in its events and in its coverage consequence. A metadata-only digital voice desk is a real capability and will be labelled as exactly that, never as a voice decode.

Transcription is being given a bound engine under the profile section chapter 05 already defines, designed to run in the background, never to block collection, and to report its backlog in the products. Transcripts will carry their machine marking and quality note wherever they appear. Two partial limits inside standing desks are also in the program: APT imagery decode for the satellite desk, so a locked NOAA pass yields an image rather than pass audio alone, and radiosonde telemetry decode for the sonde desk on the real source, so sonde windows yield tracks rather than carrier censuses.

## The airband repair and the UHF military airband desk

The airband channel set and rotation geometry are being repaired together, and the diagnosis is stated here so the repair can be judged against it. In 0.1.3 the airband rotation window is centred on 125.0 MHz at 2.4 MHz wide, spanning 123.8 to 126.2 MHz, while the desk's six channels spread from 118.700 to 127.800 MHz. Four of the six channels fall outside the captured window on every airband dwell and are dropped by the channelizer, a defect visible in the dwell records' dropped lists. The repair aligns the channel set with the sector frequencies of the region the watch serves, restructures the airband dwells so every configured channel falls inside a captured window, and raises the airband and ADS-B rotation weights so aircraft coverage is minutes apart rather than the better part of an hour apart.

A new UHF military airband desk is being added on the same occasion. Military aviation voice works the UHF band in AM, a band the current build visits only as a sweep window at 255.4 MHz, where an emission is an observation and never a voice. The new desk is intended to work the UHF airband channels as a voice desk in its own right, with segments, snippets and guard handling on 243.0 MHz mirroring the VHF desk's discipline on 121.5 MHz.

## The universal audio emissions census

The program's flagship item is a feature in development: a universal audio emissions census. The gap it addresses is structural. In the current build, audio is heard only where a voice desk is already pointed: the airband channels, the weather radio channel, the satellite pass window. An audio-bearing emission anywhere else in the captured spectrum, on any rotation or sweep window, is recorded by the sweep desk as an observation with a shape, and no more is learned about it.

The census is intended to close that gap on every captured window. Each window is to be channelized and examined for audio-bearing emissions, in AM, FM and SSB, whether or not any named desk claims that spectrum. A detected emission is to be demodulated, filed as an audio snippet under bounded quotas, and reported as an event carrying its frequency, estimated modulation, signal to noise ratio, duration and a voice-likelihood grade, with a dossier so a recurring unknown voice accumulates a history. Where a named desk can decode the emission, the decode proceeds as now and the census defers to it. Where none can, the emission is reported as unknown audio, in those words, and never dressed as speech it was not shown to be. The intended end state is a simple guarantee, stated as a goal and not as a current capability: if audio is being transmitted that the receiver can discern, the watch knows about it and says so.

## License

MULTIDEX is free software, released under the GNU Affero General Public License, version 3. The full license text ships with the repository as its LICENSE file and governs every copy, modification and network use of the system. This manual is part of the same work and is published under the same terms.

# 14 Live updates

A standing watch no longer has to stand down to take a new release. From 0.7.0 an installation can be laid out as a set of immutable generations, and a watch can hand over from one generation to the next at a dwell boundary, mid-watch, with the bill, the Index and the coverage account continuous across the change. This chapter describes the mechanism exactly as it is built: what is verified, what is never trusted, how a handover happens, how a bad release is recognised and reversed, and how the operator gets out by hand when the machinery itself is the problem.

The machinery is conservative on purpose. A release is adopted only after its signature verifies, its ancestry and release counter check out, its diff passes an audit, a full simulated watch runs under it, and it proves it can read the standing home's record. A release that clears all of that still serves a probation before it is believed. Anything that fails is marked bad in the home and is never retried; the way forward is always a later release, never a second attempt at the same one.

## The generation layout

A generation layout is an install root holding three things. The repo folder is a clone of the release repository, used only to fetch and verify. The releases folder holds one folder per generation, named by the full commit hash of the release it contains; a generation folder is written once, verified file by file against the commit, and never modified afterwards. The current link points at the generation the keeper will start next.

Running from a generation is ordinary: the officer records its own generation hash at startup, from the resolved path of its own code, and files it in the watch state and the muster record. What is running is always read from the running process, never inferred from the pointer or the link, because both can move while a watch stands.

A plain single-tree checkout, the layout this system has always deployed as, keeps working unchanged. The update machinery engages only where a generation layout exists, and doctor says which mode an install is in. The keeper bootstrap in the keeper folder converts a clone into the layout: it keeps the clone as the repo, copies the keeper out to the install root where no release can overwrite it, materialises the current commit as the first generation, verifies it, and writes the pointer.

## The keeper

The keeper is a small standalone program in the keeper folder, deliberately not part of the package. It does no network work, no git, no verification and no device work. Its whole charge is supervision: it reads the pointer state file in the home, starts the officer for a generation, watches the watch state file's heartbeat and the officer's process liveness, performs handovers and probation rollbacks, and can kill an officer that hangs without releasing the tuner. Stopping the keeper does not stop the watch; the officer it started keeps standing, and a keeper started again picks supervision up from the record.

The pointer file records the current generation and its status, the previous generation, any probationary generation with the watch and snapshot that probation belongs to, the set of generations marked bad, the times of failed activations, and whether automatic posture stands degraded. It is written atomically, carries a checksum, and keeps a backup copy of the last good write beside it. A corrupt pointer is recovered from the backup; a pointer and backup both corrupt is a stop condition, and the keeper refuses with the escape hatch named, because guessing at which generation should hold a tuner is not a recovery.

## Fetch, verify, stage

On the profile's check interval, and on demand through the update verb, a check runs in a detached worker process, never inside the officer's schedule loop. The check asserts the clone's remote still answers at the configured address: the address is pinned in the home at the first successful check, and a clone that later answers somewhere else is a stop condition, because a silently moved remote is exactly the shape of a supply-chain attack. The check fetches only. Nothing is merged, checked out or executed from the fetch.

The candidate is identified by a signed annotated tag naming a monotonic release counter. The tag's signature is verified with git verify-tag against an allowed-signers file stored in the home. The signers file never lives in any repository or generation, so no release can carry its own trust. If no signers file exists, verification fails closed and says so: there is no unsigned activation path in any posture. A release tag that has moved since it was first fetched stops the check the same way.

Four more conditions bind before a candidate is staged. Its commit must descend from the running commit, so a release is always a fast-forward of what stands. Its counter must exceed the highest counter ever recorded in the home, so an old release can never be replayed as new. It must not already be marked bad. And its diff against the running commit must pass the audit: no binary files, no submodule entries or .gitmodules changes, no symlinks pointing outside the tree, no unexpected file modes, and no change under the keeper folder, because the keeper is updated by hand, never by a release. A candidate that clears verification is materialised from its commit hash into a fresh generation folder, and every file is re-verified against the commit's own blob hashes before the generation is considered staged.

The profile's updates section carries the posture. Under automatic, a gate-passed candidate activates at the next safe boundary. Under notify-only, the shipped default, it waits for the operator's approval through the update verb. Under off, checks still run and stages still file their events, but nothing activates. Posture gates activation only, never verification: an unverifiable release is refused identically under every posture.

## The activation gates

A staged candidate must pass two gates before it may activate. The simulation gate runs the candidate's own entry point through a full simulated watch in a throwaway home, on the standing profile's position and hours at a high time scale; the watch must complete. The read-proof then runs the candidate against copies of the real home - copies, never the originals - and requires that the candidate read the standing record and arrive at the same adoptable summary: the same watch, at least the same executed dwells, event and dossier counts, and ledger grants, and a ledger format inside the candidate's declared readable range. The proof exists because the failure it prevents is the worst one available to an update system: a new release that cannot read the old release's record, adopting a watch it cannot account for.

A candidate that fails either gate is marked bad with the reason, in the home's update state and in the Index. A candidate whose declared writable ledger or event-log format exceeds the standing home's written format is a different case: it is blocked, not bad, in every posture, with the reason stated. Raising a state format is a manual, snapshot-first upgrade performed by the operator standing the watch down; it is documented here as a procedure the system deliberately does not automate, because a format raise that goes wrong mid-watch has no honest rollback. The state rules otherwise stay additive: new fields may appear, old readers ignore fields they do not know, and the event log's evolution rule is unchanged.

## Handover and probation

Activation happens only at a dwell boundary, and only when the keeper supervises the watch. The running officer finishes its item, and before the handover it checks the pass guard: no handover flies when the next bill item is a satellite pass, or when a pass begins inside the guard window, thirty minutes by default, because a pass is the one item whose timing cannot be re-planned around a restart. A deferred handover is silent and retried at the next boundary.

Before the old officer exits, a snapshot is taken into the home: the allocation ledger and the SQLite index, the index copied through its own backup interface rather than as a raw file. The officer files the handover event, writes the handover record, closes the tuner and exits without standing the watch down. The keeper starts the new generation's officer, which adopts the watch through the ordinary resume machinery - the muster gates re-run, and the tuner gate among them is the device self-check - and files the flip. Desk workers are spawned from the officer's own generation tree for the whole of an item, by construction, so no worker of the old generation survives into the new one's items; workers in flight at a boundary drain under the supervisor's existing rules and are never killed by the handover itself.

The new generation then serves probation: the first three ordinary items by default. Probation watches process liveness, the advancing event log, each item's normal completion record, and the gap record - an item that files an officer error, a capture failure or a tuner loss fails probation, while a single desk's failure does not, because the supervisor's benching already answers for a desk. Probation passed marks the generation successful in the pointer and drops the snapshot. Probation failed is a rollback: the keeper restores the snapshot, flips the pointer and the current link back, marks the generation bad, records the failed activation, and starts the previous generation, which resumes the watch from the restored record and files the episode. Items flown under the failed generation are flown again under the good one; the snapshot discipline makes that the honest outcome rather than a loss.

Three failed activations inside twenty-four hours degrade automatic posture to notify-only, in the pointer, until the operator resets the failure record through the update verb. The cap exists so a run of bad releases costs an operator decision, not a night's watch.

## Surfaces

Every stage files an event in the Index under the update kind: offered, verified, staged, gate passed, gate failed with the reason, blocked on format, marked bad, flipped, probation passed or failed, rolled back, handover waiting, posture degraded. The status verb gains an update account: the running generation and its version, the posture in force, the highest counter seen, the last outcome, and the fallback generation. Doctor gains an updates account: the layout mode, the keeper state, whether the signers file is present, and a freeze warning when no successful remote check has landed inside the configured days, fourteen by default, because an updater that has silently stopped reaching its remote is itself a failure worth surfacing. The update verb checks now, shows status, approves a staged candidate under notify-only, resets the failure record, and rolls back manually; a manual rollback during a watch is written as a request the officer honours at the next safe boundary, and with no watch standing it flips the pointer at once.

## The escape hatch

The machinery is not a trap. To get out by hand: stop the keeper, then run any retained generation's officer directly from its releases folder, exactly as a single-tree install runs; the watch resumes from its record under that generation. If the pointer is corrupt, restore it from the backup copy beside it. If a rollback's snapshot is needed by hand, the snapshots folder in the home holds the ledger and index copies under the snapshot id the handover event names. No step needs the network, the repository, or a working updater.

## Honest limits

Four limits are stated plainly. The handover closes the tuner and the successor reopens it; that reopening must be measured on a host before the first live handover there - a host whose tuner reopens unreliably will fail probation honestly, but the measurement is a deployment gate, not something the system can certify from inside. The keeper is one supervisor: if it dies, the watch stands unsupervised until it is restarted, and handovers wait. Releases arrive only from the configured remote, and the system's whole trust rests on the signers file in the home; whoever can write that file can name a trusted release, so it is operator-held state and is guarded like it. And the keeper itself, standing outside the generation machinery, is updated by hand, on purpose: the component that performs rollbacks is never itself rolled forward by the thing it rolls back.

Free Computation

