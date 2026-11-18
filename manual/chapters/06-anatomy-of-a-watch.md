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

