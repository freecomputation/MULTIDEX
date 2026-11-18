# 08 Products

A watch exists to produce its products: the Index and its dossiers, the event record, FLASH alerts, SITREPs, the Watch Report, the snippet store, and the ask surface that queries all of it. Every number in a rendered product comes from the Index at render time, and every product carries or points at its coverage account in its first lines. Products are plain Markdown, written to the watch folder, and they remain in the home after the watch ends.

## The Index and dossiers

The Index is the durable store, held in the data home. Its ground truth is an append-only JSONL event log per watch, events.jsonl in the watch folder; beside it sits a SQLite database in the home, rebuilt from the logs, which serves queries. Rebuilding preserves the snippet accounting, the event links and the watch metadata, which the logs themselves do not record, so the log remains the evidence and the database a faithful index over it. Beside the Index in the home stands one further record, the allocation ledger, allocation-ledger.json: the standing account of the reception pool that governs all filed evidence, described below. It belongs to no watch, and it is the system of record for every grant, consumption and release.

Every event carries the same fields: id, watch id, desk, kind, start and end times, frequency where one applies, emitter where one is identified, confidence grade, content in plain words where the desk produced any, a desk-specific data record, the snippet path where evidence was filed, and links to related events. The kinds are decode, observation, voice, audio, alert, pass, imagery, telemetry, census, dwell, gap, desk_health, assessment, system and update. Grades are enforced at the point of writing: an event with a grade outside CONFIRMED, PROBABLE and UNRESOLVED cannot be created, so it can never reach the Index.

Emitters accumulate dossiers. A dossier key is the emitter's canonical identity, and aliases resolve to it: an aircraft's ICAO address, tail number and flight callsign are one dossier across desks, and a vessel's name and callsign resolve to its MMSI dossier. A dossier records its kind, first and last hearing, the watches it appears in, its event count and its aliases. Dossiers persist across watches, which turns a sequence of 24-hour watches into a standing picture of a place: the regulars become known, and the exploitation layer can say, with the record behind it, that a regular is missing or a stranger is new.

## FLASH alerts and the chime

A FLASH is the watch's interrupt to its operator: a single file, FLASH-001.md onward in sequence, written the moment a standing trigger crosses. The triggers are fixed by the design and extended by the profile. An alert-kind event always raises one, covering SAME weather alerts and guard frequency activity. An exploitation assessment at or over the profile's threshold grade, PROBABLE as shipped, raises one. A completed pass the desks flag, such as a sonde flight reaching burst, raises one. A satellite pass event graded CONFIRMED raises one, and a decoded NOAA pass carries its imagery on that same event. An event from an emitter on the watch_emitters list raises one. Voice or observation activity on a watch_freqs_hz frequency raises one, which is why the shipped profile lists the 121.5 MHz guard frequency there.

The FLASH file states what happened, where in frequency and band, when, the trigger, the confidence grade, and the ids of the events behind it. Sequence numbers are never reused, including across a resume: a resumed watch continues numbering from the files on disk.

When the profile's alerts chime is true, as shipped, a two-tone chime sounds on the host after the FLASH file is written. The chime is the watch's only sound. Its outcome is recorded in the triggering event's data, played or not played, so the record of an alert includes whether anyone in the room could have heard it announced. A FLASH is never blocked by the chime: with no audio player on the host, the FLASH records that it was not played and stands.

## SITREPs

A SITREP is the periodic situation report, rendered automatically every sitrep_hours of watch time, 6 hours as shipped, and on demand with the sitrep verb. Files are SITREP-1.md onward in the watch folder. A SITREP opens with its period and source line, then states the period's coverage in one paragraph: dwell time executed across how many dwells, and how many gaps, with bands not visited deferred to the Watch Report's account. It then gives the period's population, distinct emitters per desk; the notables, the period's alert, assessment and pass events up to twenty; the FLASH roll for the watch so far; desk health for the period, or the statement that all standing desks were healthy; and the day picture so far: events, passes taken and missed, alerts and assessments. At each automatic SITREP boundary the exploitation layer also runs its missing-regulars check, so an emitter that should have been heard and was not is assessed at the boundary rather than discovered at stand-down.

## The Watch Report and its coverage account

The Watch Report, REPORT.md in the watch folder, is the flagship product, rendered at stand-down over the whole watch or over the elapsed portion of an early stand-down, which it states. Its coverage account is the third law discharged: dwell time executed against dwell time planned, the gaps with their causes, the bands visited and never visited from the band plan, the desks benched with their reasons and the stated consequence of each bench for the picture the watch can present, and the source line, which names the source flown and carries the substitution note in full where the real source could not be stood. A reader who trusts nothing else in the report can audit the account against the dwell and gap records in events.jsonl, the same records the account was computed from. Beyond the account, the report presents the watch's population and assessments: dossiers heard, passes taken and missed, alerts raised, and the exploitation assessments with their evidence links. It also renders the watch's reception account, this watch's slice of the standing allocation ledger: the bytes it was granted, what those grants consumed, what was released unspent back to the pool and what was shed, in total and per class, against the standing cap. The ledger is the record; the account is its rendering for this watch.

## Snippets, grants and the standing pool

Snippets are the filed evidence: voice audio from the voice desks, I/Q captures of unidentified or anomalous emissions, satellite pass audio and decoded imagery, SSTV images, and the digital voice desk's payload frame archives, kept in the watch folder's snippets folder. Every snippet filename carries the watch id and the bill item id, so two dwells can never produce the same path and one event's evidence can never overwrite another's.

Keeping is governed by the standing allocation ledger, not by any single watch. The pool is a property of the home: its cap is the profile's disk ceiling, 20 GB as shipped, read as a standing cap on everything the home's watches file, bounded by the volume's actual free space, and a margin of five percent of the cap is never granted to anyone. The ledger that accounts for the pool stands beside the Index in the home and belongs to no watch. Doctor and status state the pool from it, cap, consumed, reserved, free and margin, whether or not a watch stands.

Reception is granted, not assumed. At muster the watch takes a grant for every bill item at worst-case bytes: capture arithmetic for captures, a predicted APT pass at its window length times 22,050 bytes per second plus a 4,000,000-byte allowance for the decoded image, an audio desk charged at its capture rate for the whole dwell, an event-only desk at a flat 262,144 bytes, and the audio census charged on every window item it rides. When the pool cannot carry the bill, grants shrink in a fixed order, Class C first, then Class B, then Class A, satellite passes last, every grant floored at 1,000,000 bytes, and when even the floored bill exceeds the pool the overage is stated in the muster record. APT passes also hold standing reservations, taken over a 48-hour horizon in window order while the pool can carry them, so a pass ahead holds its room between watches; a watch that bills a reserved pass adopts its reservation, and an adopted reservation the watch does not draw reverts to standing when the watch releases it. METEOR passes stand outside all of this: zero bytes, no reservation, no grant, and any grant an older build left for one is released when the ledger is next loaded, with the release stated. An interrupt takes its grant mid-watch on the same terms, whole, shrunk to the grantable room, or refused under the floor with the reason filed.

Filing draws against the grants. The count caps still bind inside a watch: voice snippets are capped per voice desk per watch at voice_cap_per_desk, 200 as shipped, I/Q snippets per watch at iq_max_per_watch, 100 as shipped, and satellite material is kept whole when satellite_keep is true. A filing past a count cap is refused with the reason filed as an event, and a payload that would cross its bill item's grant is refused at filing with the grant named. Consumption posts against the grant that carried the filing. When the pool itself binds, the store sheds the oldest filed receptions across all watches, not merely the current one, satellite evidence shed last, and a shed posts against the victim's grant, naming it, with a system event for every shed, so the evidence record degrades in the open and the products never claim evidence no longer on disk. One class never sheds: payload snippets, the vocoder frame archives, are filed even when nothing sheddable remains, and the overage stands in the accounting.

The account closes cleanly. Stopping or cancelling a watch releases all its unspent grants back to the pool at once, while the bytes it consumed stay in the ledger's history. Grants orphaned by a watch that died are reclaimed at the next muster, and a resumed watch reconciles its grants against the ledger, its bill and the snippet store before it flies again. The budget.json in a watch folder is a snapshot of the grants that watch took at muster, a view rendered for the folder; the ledger is the record.

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

