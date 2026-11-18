# MULTIDEX - Design

Status: design, written 2026-11-17. No code exists. Nothing has been pushed to the repo beyond what the operator placed there. Build begins only on the operator's word.

## 1. Purpose

MULTIDEX is an autonomous signals watch system built on a single RTL-SDR. The operator issues one small command and the system does the rest: it plans a 24-hour watch, schedules the one tuner across everything that tuner can reach, collects, decodes, classifies, correlates, and disseminates finished products, all without further human direction. The operator may configure the watch in advance through a profile. Configuration is an option, never a requirement - the shipped defaults are sane, complete, and sufficient for a full watch on the day the system is first run.

The model is the intelligence cycle run by one machine: collection management, collection, processing, exploitation, dissemination. A mini dissemination system in the proper sense - raw signal goes in one end, finished product comes out the other, and the product, not the waterfall, is the point. It is extremely complex inside and deliberately simple at the surface: one command to start, one to ask where things stand, one to stop.

MULTIDEX is a third thing. It is not an engine under Passband or KNELL and it does not absorb them. Passband works a schedule of known services as an instrument the operator drives. KNELL censuses the unknown as an instrument the operator drives. MULTIDEX is the watch officer: it stands a full day watch on its own authority under the operator's standing orders, and it answers for the whole spectrum the kit can reach, not for one service or one question. The three programs share a host and a dongle and nothing else - no code, no store, no schedule. Contention for the tuner is governed by the sovereignty law in section 3.

## 2. The name

MULTIDEX: the index of many. Many bands, many modes, many emitters, one index. The working expansion proposed by this design, for the operator to settle or replace, is Multi-band Dissemination and Exploitation Index - MULTI for the many bands and modes under watch, D for dissemination, EX for exploitation, and the whole word landing on what the system keeps: an index of everything it heard, resolved to the emitters that made it.

Collision note, recorded at founding: no signals or radio product uses the name. It is occupied elsewhere - Android's Multidex framework in software development, and Wilson Jones MultiDex index dividers in office products. The operator founded the repo under this name with that disclosed.

## 3. Laws

These laws govern every later section. Where a feature and a law conflict, the law wins and the feature is cut.

- First law, autonomy: a started watch runs to completion with no human input. Anything that would need the operator mid-watch is either decided in advance by the profile, decided by the defaults, or deferred to the products. The system never pages the operator to ask a question.
- Second law, honesty: the system never invents content. A decode is reported at the confidence the evidence supports, a transcription is marked as machine work with its uncertainty, an unidentified emission stays unidentified, and static is never dressed up as a voice. This is the KNELL discipline carried in full.
- Third law, coverage: every product states what was not heard and why. One tuner cannot be everywhere, so every report carries its coverage account - where the tuner was, where it was not, and what classes of event the watch therefore cannot speak to. A product without a gap statement is a defect.
- Fourth law, one tuner: the design never pretends the kit is more than it is. One RTL-SDR, one window of spectrum at a time, and the whole art of the system is how it spends that window across 24 hours. Capabilities that need two tuners or transmit capability are out of scope, not faked.
- Fifth law, sanity at the gate: a watch starts only when the system has verified its own footing - tuner present and free, clock true, disk headroom real, profile valid. If the footing fails, the system refuses with the reason in plain words and starts nothing. A half-watch begun on a bad footing is worse than no watch.
- Sixth law, locality: everything stays on the operator's machines. No network dependence, no accounts, no telemetry, no product leaves the host except by the operator's own hand. The home position and all logs are local data, handled under the standing scrub discipline and never published.
- Seventh law, sovereignty: the operator's stop is immediate and final, and the tuner is the operator's. If the operator or another of his programs takes the tuner, MULTIDEX stands down and records the gap - it never fights for the hardware, never holds it with a test process, never restarts itself into contention.
- Eighth law, receive only: there is no transmission path in this design, in any desk, at any tier. Encrypted traffic is metadata - presence, timing, volume, endpoints where broadcast - and its content is out of scope by construction, not by policy toggle.

## 4. Hardware basis

The design targets the kit the operator owns, and nothing else.

The receiver is an RTL-SDR Blog V4: RTL2832U with an R828D tuner, covering 500 kHz to 1.766 GHz, with a built-in HF upconverter ahead of the tuner (28.8 MHz LO, triplexer, AM/FM/DAB notch filters), a 1 ppm TCXO, and a bias tee. It delivers one stream of I/Q at up to about 2.4 MHz of usable bandwidth (3.2 MHz is reachable with dropped samples and is not used for watch work), at 8 bits per sample, giving roughly 50 dB of instantaneous dynamic range. Gain is a design variable, not a fixed number: the in-house finding stands that gain 23 suits the operator's site on the bands Passband works, and each desk in section 7 carries its own gain and its own AGC policy because a setting that serves airband starves HF.

The host is the ThinkPad X200 by default: always on, low power, Core 2 Duo, 8 GB of RAM, running Trisquel. The EliteDesk G1 is the alternate host. The X200's CPU is the binding resource after the tuner itself, and the design budgets for it honestly in section 14: channelization and most decoders are cheap, a few desks are heavy, and machine transcription of voice is measured in-house at roughly 19 times slower than realtime on this host, so transcription is treated as background work that may lag the watch and is never allowed to block collection.

Time is hardware. Satellite passes, sonde windows, and every timestamp in the index depend on a true clock, so the muster check in section 6 requires the host clock to be within one second of network time at watch start, and the watch records its own clock drift if time service is lost mid-watch.

Disk is budgeted, not assumed. The decoded store is small. Audio and I/Q snippets are the mass, and both are capped by policy in section 8: the default watch ceiling is 20 GB, enforced by the system, with the arithmetic shown in section 14. A watch that would overrun its ceiling sheds snippet capture first and says so in its products; it never fills the operator's disk.

Antennas are the operator's existing antennas. The design does not assume a discone on the mast or a long wire in the yard; it assumes what is connected is what there is, and the muster records which antenna was on the tuner for the watch, because the coverage account in the products is meaningless without it.

## 5. Architecture

Five organs, one daemon, no shared state with any other program.

- The Watch Officer is the scheduler and the system's only decision-maker. It holds the watch bill (section 6), assigns the tuner minute by minute, opens and closes dwells, triggers interrupts, and signs every product. Everything the system does, the Watch Officer ordered.
- The Tuner Server is the sole owner of the dongle. It takes tuning orders from the Watch Officer and gives back one I/Q stream. Nothing else in the system touches the hardware, which is what makes the sovereignty law enforceable: one throat to choke, one place where a stand-down happens.
- The Desks are the decoder modules of section 7. A desk is a worker that knows one service or one mode family: what frequency, what bandwidth, what demodulation, what the decoded output means. Desks receive channelized audio or I/Q from the Channelizer, never the raw tuner, and they emit normalized events and nothing else. A desk cannot retune the radio, cannot write outside its own output, and can be benched by the Watch Officer without disturbing the watch.
- The Channelizer sits between the Tuner Server and the Desks. Within one tuned window it cuts out as many virtual receivers as the window and the CPU allow - the airband stack, both AIS channels, a cluster of ACARS frequencies - and feeds each desk its own narrow stream. This is how one window becomes many simultaneous services, and it is the main force multiplier the design has.
- The Index is the store and the memory: every event, every emitter dossier, every baseline, every snippet, kept in one queryable whole (section 8). The Index outlives the watch. Each new watch opens against the standing Index, which is how the system knows what is new.
- Dissemination is the product organ: it renders FLASH alerts, SITREPs, the Watch Report, and the query surface from the Index (section 10). It reads the Index and writes files. It has no path to the tuner and no path off the machine.

Process model: one supervising daemon. Desks run as subprocesses with a narrow pipe contract - channelized samples in, JSONL events out - so a desk crash is a subprocess restart, not a system failure, and a new desk is a new program that speaks the pipe, not a change to the core. The core is small and readable on purpose, in the house discipline: the Watch Officer, Tuner Server, Channelizer, Index writer, and Dissemination renderer are the whole trusted base.

## 6. The watch

A watch is 24 hours, from the minute the operator starts it, in three movements: muster, the watch proper, and stand-down.

Muster takes under two minutes and is the sanity gate of the fifth law made concrete. The system verifies the tuner is present and free, the clock is true, the disk ceiling is reachable, the profile parses, the home position is set (satellite and sonde work is impossible without it), and the antenna note is recorded. It then builds the watch bill: it computes the day's satellite passes from current orbital elements, marks the sonde windows, lays the standing rotation over the 24 hours with propagation awareness, and writes the bill to the watch folder before the first tuning. If any gate fails, the operator gets the reason and nothing starts.

The watch bill is the plan the Watch Officer executes, in four priority classes.

- Class A, clock events, own the tuner by the clock. Satellite passes are the type case: a NOAA or Meteor pass is ten minutes that will not come again, with the tuner reserved from two minutes before acquisition of signal to loss of signal, the desk pre-staged, Doppler tracked from the ephemeris. Radiosonde windows are the second: balloons rise ahead of the 00Z and 12Z synoptic hours and fly for about two hours, and the regional launch sites configured in the profile define where the sonde desk dwells. ISS passes, including scheduled school contacts and SSTV events when the ephemeris and the event calendar say so, are Class A as well.
- Class B, the standing rotation, is where the tuner spends most of its day: weighted dwells across the services that reward regular visits - ADS-B, the airband stack, ACARS and VDL2, AIS, APRS, weather radio, the ISM bands, the pager bands, the amateur bands in their turn. Weights live in the profile and the defaults are set for a general picture: no single service starves, no single service monopolizes, and each dwell is long enough to mean something - a 90-second ADS-B dwell counts aircraft, a 20-minute airband dwell hears a conversation pattern, and the bill respects the difference desk by desk.
- Class C, census sweeps, fill every gap the other classes leave. The sweep is the KNELL motion inside this system: wide, patient, logging every emission it crosses into the Index as an observation, building the day's spectrum picture band by band. The deep sweep is scheduled in the small hours, 01:00 to 04:00 local by default, when the bands are quietest and an anomaly has nowhere to hide.
- Interrupts preempt Class B and C, never Class A. When a sweep or a dwell crosses an emission that is strong, new to the Index, or on a configured watch frequency - the 121.5 guard is the standing example - the Watch Officer breaks the plan, dwells on the emission, classifies it as far as the desks can, captures its snippet, writes it up, and returns to the bill. An interrupt is the system paying attention, and its budget is capped per hour so a noisy band cannot hijack the watch.

Propagation writes the day plan's shape. HF follows the sun: the higher HF bands are morning and afternoon desks, the low bands belong to the night, and the bill re-weights the amateur and HFDL dwells by the hour rather than pretending 20 meters at midnight equals 20 meters at noon. VHF and UHF services run all day with traffic-shaped weights: airband and ADS-B heavy in the flying hours, weather radio steady, the ISM bands steady, marine AIS steady. The default day therefore has a rhythm - a morning picture, a midday rotation, an evening peak, a deep-sweep night - and the Watch Report narrates that rhythm because the rhythm is itself a finding.

A dwell, the atomic unit, runs the same way every time: retune, settle for a measured beat while the tuner stabilizes, set the desk's gain, channelize, run the desks for the dwell's length, close with a dwell record that states what was heard, what was decoded, and what the noise floor did. Dwell records are what the coverage account is built from, so a dwell that heard nothing still files its record. Silence, honestly logged, is data.

## 7. The desks

A desk is specified by eight things: its service, its frequencies, its window, its demodulation, its decoder basis, its normalized output, its CPU class, and its snippet policy. The inventory below is tiered by evidence, in the house manner: Tier 1 is proven on the operator's kit or in the house, Tier 2 is established open-source practice awaiting integration, Tier 3 is survey-grade and reports metadata and presence only. No desk is claimed above its tier, and the Watch Report prints each desk's tier beside its results.

Tier 1 - proven ground.

- ADS-B desk. 1090 MHz, the readsb line the house already runs. Output: aircraft positions, identities, velocities, as events keyed by ICAO address. CPU light. No snippets; the decode is the record.
- Airband desk. 118.000 to 136.975 MHz, AM voice, channelized into as many simultaneous channels as the window holds. Output: transmission events with channel, duration, and signal quality; audio snippets kept by policy; transcription as a separate, bounded, background product (section 9). CPU moderate, transcription heavy and queued.
- ACARS desk. The VHF ACARS cluster around 131.550 MHz. Output: message events keyed by aircraft tail. CPU light.
- AIS desk. 161.975 and 162.025 MHz together in one window. Output: vessel events keyed by MMSI. CPU light.
- Weather radio desk. 162.400 to 162.550 MHz. Output: broadcast presence, SAME alert headers decoded as events, alert audio kept whole - an alert is short and is exactly what snippets are for. CPU light.
- ISM desk. The rtl_433 ground the house knows: 315, 433.92, 868, and 915 MHz device chatter. Output: device events keyed by device identity - weather stations, meters, tire sensors, remotes. CPU light.
- APRS desk. 144.390 MHz packet. Output: position and message events keyed by callsign. CPU light.
- Pager desk. POCSAG on the configured paging frequencies, FLEX where the decoder holds. Output: message events keyed by pager address, content as decoded, marked as broadcast traffic. CPU light.

Tier 2 - established decoders, integration owed.

- VDL2 desk. 136.975 MHz. Output: datalink events keyed by aircraft. The natural sibling of ACARS, and together they thicken every aircraft dossier.
- Weather satellite desk. NOAA APT at 137 MHz and Meteor LRPT at 137.9 MHz, Class A scheduled. Output: received imagery filed as a product of the pass, with the pass geometry and the decode quality stated. This desk is the watch's showpiece and its strictest scheduler test.
- Radiosonde desk. 400 to 406 MHz, Class A windows. Output: sonde events keyed by serial - position, altitude, telemetry - and a flight track in the Index. A sonde received from launch to burst is a complete story the watch can tell end to end.
- Amateur desk. SSB and CW voice and the digital family - FT8 and its siblings first, then the wider set as integration allows - across the HF bands by the propagation-weighted bill, plus VHF/UHF repeaters in the rotation. Output: contact events keyed by callsign, with mode and band; FT8 decodes are text by nature and file as content, voice files as snippets with transcription queued like airband.
- Digital voice desk. DMR, P25, and NXDN where unencrypted, on configured trunked and conventional frequencies in the rotation. Output: call events with talkgroup and radio identifiers as metadata; voice snippets by policy. Encrypted talkgroups file as presence only, per the eighth law.
- HFDL desk. The HF datalink channels by the propagation bill. Output: message events keyed by aircraft, the oceanic counterpart of ACARS.
- Broadcast desks. FM with RDS and AM/MW, in light rotation. Output: station census events - what is on the air, with what identity, at what strength. Low intelligence value per hour, near-zero cost, and they anchor the band picture the sweeps build on.
- ISS desk. 145.800 MHz voice and SSTV, Class A on passes, event-driven on scheduled contacts. Output: pass events, imagery where SSTV runs, voice snippets by policy.

Tier 3 - survey only, metadata and presence.

- Cellular survey desk. GSM and LTE downlink census in the rotation: which cells, on which bands, at what strength, identifiers as broadcast. No content exists for this desk by construction.
- Trunking census desk. Control-channel discovery and system census during sweeps: systems present, sites, talkgroup activity counts as metadata, feeding the digital voice desk's configured list over time.
- Spectrum anomaly desk. Not a decoder: the sweep's own analyst, flagging emissions that match no desk and no Index entry for the exploitation layer. This is the desk that hands interrupts to the Watch Officer, and the bridge between the census motion and the rest of the system.

Every desk files failures as events. A decoder that exits, a dwell with an impossible noise floor, a pass the weather satellite desk could not lock - all of it lands in the Index and surfaces in the products. The watch's credibility rests on its failures being as visible as its catches.

## 8. The Index

The Index is one store with four contents: events, dossiers, baselines, and snippets. Its native form is an append-only JSONL event log per watch plus a SQLite database the log rebuilds and the queries run against; the log is the truth, the database is the convenience, and either can be regenerated from the other in the log's direction.

An event carries: a unique identifier; the watch identifier; start and end time in UTC with the host clock's stated quality; frequency or channel; the desk; the mode; the emitter key where one exists; the decoded content where the desk produces content, verbatim; a confidence grade from the three-grade scale in section 9; a snippet reference where a snippet was kept; and links to related events, written by the exploitation layer as it correlates. An observation from a sweep - an emission with no decode - is an event too, with its shape recorded: center, width, strength, duration, modulation guess at its honest grade.

A dossier is the Index's memory of one emitter, keyed by the strongest identity the emitter broadcasts: ICAO address and tail for aircraft, MMSI for vessels, callsign for amateurs and stations, device identity for ISM gear, serial for sondes, address for pagers, cell identity for the survey desk. A dossier holds the emitter's event history across all watches, its first and last hearing, its habits as the baselines learn them, and its cross-links - the tail that spoke on ACARS, flew on ADS-B, and was heard on the airband desk is one dossier with three kinds of evidence in it, which is the single most valuable thing the Index does.

Baselines are the learned normal: for each band and service, per hour of day, the expected population, activity level, and noise. A baseline is built from the watches themselves, is marked provisional until three full watches support it, and is never silently trusted - every anomaly claim in a product names the baseline it deviates from and the baseline's age in watches.

Snippets are the kept evidence: audio of transmissions, I/Q of unidentified emissions, imagery from satellite passes. Policy, in the profile with sane defaults: voice transmissions kept whole up to a per-desk cap; I/Q kept only for unidentified or anomalous emissions, ten seconds at a time, capped per watch; satellite imagery kept whole, it is small and it is product. The 20 GB watch ceiling is enforced across snippets with per-desk quotas, oldest-first shedding inside the current watch only, and every shed is logged so the products can say what evidence was let go.

Retention: the Index is permanent by default. Events and dossiers are small and they are the system's memory; a watch that cannot remember yesterday cannot call anything new. Snippets are the only content with a retention knob, and the default keeps them.

## 9. Exploitation

Exploitation is the layer that turns events into understanding, and it runs continuously during the watch, not as an afterthought at stand-down.

Confidence discipline comes first, because everything else inherits it. Three grades, used everywhere including inside the Index: CONFIRMED, a decode or identification the evidence carries outright - a checksummed ADS-B frame, a callsign read from a digital decode; PROBABLE, a strong match with a named gap - a modulation classified by shape, an emitter matched by habit; UNRESOLVED, recorded and honestly open. Transcriptions carry their own marking as machine transcripts with a quality note, and a transcript is never quoted in a product without that marking. The second law is enforced here, at the point of writing, not by an editor afterward.

Correlation is the layer's daily work, in four standing forms. Identity correlation joins events across desks by emitter key, building the dossier's single story. Temporal correlation joins events that explain each other in time: the sonde that rose as the window opened, the aircraft that checked in on voice as its datalink went quiet. Population correlation maintains the day's census per service - how many distinct aircraft, vessels, stations, devices - which is the number most products lead with. Negative correlation watches for the regular who did not appear: a baseline is what makes a silence noticeable, and a missing regular is a finding, filed at PROBABLE with the baseline named.

Anomaly detection runs against the baselines with a short, explicit rule set, each rule firing an assessment event rather than a conclusion: a new emitter in a learned population; a known emitter far outside its hours, band, or power; a new emission shape in a quiet band; a regular gone silent; activity volume beyond the baseline's spread. Assessments are what interrupts and FLASH alerts are made of, and every assessment carries its evidence links so the operator can audit the system's judgment from the product alone.

The day picture is exploitation's standing product-in-progress: a running account, updated as the watch proceeds, of who was up and when - the air picture over the day, the marine picture, the device population, the amateur bands' health, the satellite passes taken and missed. SITREPs and the Watch Report are both rendered from the day picture plus the Index, which is why a SITREP costs the system nothing to produce at any hour the operator asks.

## 10. Dissemination

Products are the system's reason to exist. All products are files in the watch folder, plain markdown in the house hand - plain sentences, dash-led lines where a listing is needed, no ornament - with the Index database alongside them. Nothing is transmitted anywhere. The operator reads the watch folder the way an analyst's customer reads a product line.

Four products, in precedence order.

- FLASH is the alert product, written the moment an assessment crosses the profile's FLASH threshold. Standing defaults: activity on the 121.5 guard; a weather radio SAME alert decoded; a satellite pass captured or a sonde flight completed; a new strong emitter assessed PROBABLE or better in a quiet band; a configured watch-list emitter heard. A FLASH is five lines at most: what, where, when, confidence, and the event links. FLASH files accumulate in the watch folder in time order and are folded into the next SITREP, so nothing alerted is ever lost in a summary.
- SITREP is the periodic product, rendered every six hours of the watch by default and on demand at any time. One page: the coverage account for the period, the population counts, the notables with their confidence grades, the FLASH roll, the desk health account, and the day picture so far. A SITREP is written to be read in two minutes and to make the full report unnecessary for anyone who only needs the state of the watch.
- The Watch Report is the flagship product, rendered at stand-down, and the standard the whole system is judged by. Its chapters: an executive summary of the day in plain language; the coverage account for the full 24 hours, including every gap and its cause; the day picture, service by service; the emitter index delta - new dossiers opened, dossiers changed, regulars gone silent; the anomaly chapter, every assessment with its evidence; the satellite and sonde chapter, with received imagery filed as product; transcript highlights, marked as machine transcripts; the desk health and failure account; and a statistics appendix - events by desk, decodes by confidence, dwell accounting against the bill, disk and snippet accounting against the ceiling. The report's first reader test: an operator who reads nothing else can say what the spectrum did that day, what mattered, and what the watch cannot speak to.
- The Index itself is the fourth product: the standing, queryable memory. After the watch, and during it, the operator queries the Index through the command surface - an emitter's dossier, a band's day, a service's population, every hearing of one frequency - and gets answers rendered from the database with their confidence grades intact. The Index is what makes the second watch smarter than the first, and the tenth watch a genuinely knowledgeable watch.

Product honesty rules bind all four: every product carries the coverage account or points at it in its first lines; every claim carries its grade; every transcript is marked; every number in a summary is traceable to events in the Index, and the renderer is forbidden from emitting a count it did not take from the database at render time.

## 11. The command surface

The surface is small on purpose. Seven verbs are the whole interface, and the first is the only one a watch strictly needs.

- multidex watch - run muster, and on success stand the 24-hour watch on the default profile. With --profile NAME, run on a named profile. This verb is the product promise: one command, one day of autonomous work.
- multidex plan - build tomorrow's watch bill from the current profile and ephemeris and print it, without touching the tuner. The operator reads the plan the way a captain reads a watch bill before signing it.
- multidex status - the state of the running watch in five lines: elapsed and remaining, current dwell and desk, events and dossiers so far, FLASH count, desk health. Readable at a glance, from the host or over the operator's own tailnet shell.
- multidex sitrep - render a SITREP now, whatever the cadence says.
- multidex stop - stand the watch down now: close the Index cleanly, render the Watch Report for the elapsed portion with its coverage account stating the early stand-down, release the tuner. A stopped watch still produces its report; a partial day honestly reported beats a day discarded.
- multidex ask - the Index query verb: ask followed by an emitter key, a frequency, a band, a service, or a time range, answered from the database in plain text with grades shown.
- multidex doctor - the muster checks without the watch: tuner, clock, disk, profile, ephemeris freshness, desk inventory against tiers. Doctor is how the operator verifies the kit on a quiet afternoon instead of at a watch's first minute.

No verb asks a question back. Anything a verb needs comes from the profile, the defaults, or its arguments, and anything it cannot get is a refusal with the reason, per the fifth law.

## 12. Configuration

One profile file per named profile, plain text, commented, and short enough to read at one sitting - if a knob cannot justify itself in one line of comment, it does not ship. The defaults below are the shipped profile, and they are a complete watch.

The profile holds: the home position, latitude and longitude, local only and never printed in any product beyond a region-level description; the antenna note; the watch length, 24 hours standard; the priority weights for the Class B rotation; the desk roster, which desks stand and which are benched; the FLASH threshold and watch-list - emitter keys and frequencies that always interrupt and always alert; the excluded bands, frequencies the watch never dwells on, empty by default; the product cadence, SITREP every six hours standard; the snippet policy and the disk ceiling, 20 GB standard; the transcription policy, which voice desks transcribe, bounded by default to airband and capped per watch; and the deep-sweep window, 01:00 to 04:00 local standard.

That is the entire configuration surface. Gain per desk, dwell lengths per desk, and the ephemeris source are system matters with sane internal defaults, adjustable in a second, advanced section of the profile that the default watch never needs opened. The design's test for itself: an operator who never writes a profile still gets the full system, and an operator who writes one is shaping emphasis, never repairing an omission.

## 13. Autonomy and failure behavior

A system that runs 24 hours unattended is judged by its failures, so they are designed, not discovered.

A desk that crashes is restarted once by the supervisor. A desk that crashes twice in an hour is benched for the watch: the Watch Officer re-plans the bill without it, the benching is filed as an event, and every product from that point carries the desk in its health account as down, with the coverage consequence stated - a watch without its AIS desk says its marine picture is sweep observations only. A desk that hangs is treated as crashed; every desk has a heartbeat and a watchdog, because a silent hang is the one failure that can quietly eat a watch.

Tuner loss mid-watch - the dongle drops, the USB bus resets, the host reboots - pauses collection and nothing else. The Index, the exploitation layer, and the product cadence keep their feet: SITREPs render with the gap stated, and if the tuner returns within the watch window, the Watch Officer re-musters quickly and resumes the bill from the current hour, filing the outage as a coverage gap with its cause as far as it is known. If the host itself restarts, the watch resumes from its own state on disk if the window still stands; a watch resumed is marked resumed in every later product. If the tuner never returns, the watch stands itself down at the point where the bill becomes meaningless and renders its report for the portion lived.

Contention is not a failure and is never fought. If the tuner is taken - the operator runs Passband, runs KNELL, or takes it by hand - the Tuner Server reports the loss, the Watch Officer treats it exactly as tuner loss above, and the products name it plainly as operator activity. The system holds no grudge and keeps no lock.

Ephemeris staleness is a soft failure with a hard rule: orbital elements older than seven days degrade satellite work to PROBABLE scheduling, stated in the products, and older than thirty days bench the weather satellite desk for the watch rather than let it hunt passes that are not there. The system fetches fresh elements when the network is available at muster and flies on cached elements, marked by age, when it is not - the locality law permits the fetch because it is the system's own housekeeping, and forbids everything else.

## 14. Resource budgets

The tuner budget is the watch bill itself, and section 6 is its account. The remaining budgets are stated here so the build can be tested against them.

CPU, on the X200: at most two heavy desks active at once, where heavy means the digital voice desk, the weather satellite desk during a pass, and any desk doing live demodulation of wideband signals. Channelization of a normal dwell, plus two light desks, plus the Index writer, is the standard load and must hold under half the host's capacity sustained, because a watch that cooks its host for 24 hours is a watch that fails in hour twenty. Transcription runs at lowest priority in the background, in quiet blocks the Watch Officer grants it, and its backlog is reported in the health account rather than hidden; transcripts that are still queued at stand-down are finished before the Watch Report renders, or the report names them pending with their audio filed.

Disk: the arithmetic the ceiling rests on. Decoded events run to thousands per watch and are negligible. Voice audio at narrowband rates runs about 30 MB per hour of actual transmission, and the active desks together rarely exceed a few hours of transmission in a day. I/Q snippets at ten seconds each run about 50 MB at full window rate and are capped at 100 per watch. Satellite imagery is a few megabytes per pass. The honest total for a busy watch lands between 2 and 6 GB; the 20 GB ceiling is headroom, not a target, and it exists so the ceiling is never the reason a watch loses something it should have kept.

Memory and state: the Index database for a year of weekly watches stays in the low hundreds of megabytes. All watch state lives in the watch folder and the Index directory, both plain files on disk, inspectable with ordinary tools, in the house manner - nothing about a watch is ever locked inside a running process's memory.

## 15. Boundaries

MULTIDEX stands beside Passband and KNELL and does not replace either. An operator who wants to drive a service by hand, or interrogate one unknown emission for an evening, has instruments for that, and this system does not pretend to be the better tool for a human's evening. MULTIDEX is for the day nobody is driving: the standing watch. Shared code between the three is none by design; shared decoder programs on the host are fine, because a decoder binary is a tool, not a component.

MULTIDEX is not a Palimpsest component and does not mirror into it. It is a standalone Free Computation program in the line of Passband and KNELL.

It is not a transmitter, a jammer, a network tool, or a decryption system, and no desk added later may make it one without a new design and a new founding decision. It hears what is broadcast in the clear or not at all, which on the modern spectrum is still, as section 7 attests, a very great deal.

It is not a product for other people's spectrum in other people's places in its defaults: the shipped profile is a general watch, and the home position, the regional launch sites, the local frequencies that matter - these live in the operator's own profile, on his own machine, under the scrub discipline, and never in the repo.

## 16. Build order and acceptance

Build only on the operator's word, and then in this order, each stage landing as a working system before the next begins, in the PR-first house discipline.

- M1, the skeleton watch: daemon, Tuner Server, Watch Officer with a simple bill, the ADS-B desk and the sweep desk, the Index, and a Watch Report. Acceptance: a one-hour watch, started with one command, unattended, ending in a report whose coverage account matches the dwell records exactly.
- M2, the rotation: the Channelizer with multi-VFO dwells, the Tier 1 desk roster, Class B weights, interrupts, SITREPs on cadence. Acceptance: a six-hour watch across an evening, with at least three services decoded simultaneously inside shared windows and one real interrupt handled end to end.
- M3, the clock events: ephemeris at muster, the weather satellite and radiosonde desks at Class A, imagery in the products. Acceptance: one full satellite pass and one sonde window taken on schedule inside a running watch, with the bill visibly bending around them and returning.
- M4, the memory: dossiers, cross-desk correlation, baselines marked provisional, anomaly assessments, FLASH. Acceptance: a watch that opens a dossier, joins it across two desks, and files one anomaly assessment whose evidence links all resolve.
- M5, the full roster: the Tier 2 desks integrated, transcription bounded and reporting its backlog, doctor complete. Acceptance: a 24-hour watch on the X200, unattended, inside all budgets of section 14.
- M6, the acceptance watch: a full 24 hours on the default profile, started with the single command and not touched again, judged on the Watch Report alone by the first-reader test of section 10. M6 is the definition of the system existing. Anything less is a partial build and is reported as such.

## 17. Open questions, with defaults holding

These are the operator's to settle, at founding or after. Until he does, the defaults hold and the design is complete without his answers.

- Host: the X200 is the default watch host; the G1 is the alternate. A watch is bound to one host and does not migrate mid-watch.
- The name expansion in section 2 is proposed, not settled. The program is MULTIDEX either way.
- FLASH delivery: files in the watch folder by default. Whether a FLASH also surfaces as a message to the operator through his own channels is his call; the system defaults to silence, because a watch that cries out at 3 AM had better be right, and the threshold for that is his to set after he has read a few watches' worth of FLASH files.
- Transcription: the airband desk transcribes by default, bounded; the amateur voice desk does not, until the operator says otherwise. Machine transcripts are a product with a confidence problem the design respects rather than solves by enthusiasm.
- Watch length: 24 hours is the standard the system is built around. Shorter and longer watches are the same machinery with a different number in the profile, and nothing in the design is special to the number except the day picture's rhythm.
- Product rendering: plain markdown files by default. A rendered reading page in the Coffer manner is a later refinement, not a founding need.

Build begins only on the operator's word.

## 18. The 0.2.0 build record

What 0.2.0 stood up, and what still stands benched, stated as
built. The full research record lives with the build.

- Airband repaired. The 0.1.x channel list held no local sector
  frequency and its single window dropped four of six channels
  on every dwell. The desk now serves the verified local set
  (sectors 121.1, 125.4, 133.45, 133.75 MHz; tower 135.2;
  clearance 118.075; operations 126.2; guard 121.5), rotation
  slots carry a cycle of window centres so every channel falls
  inside a captured window, and a UHF military airband desk
  serves the paired channels and 243.0 MHz guard. Aircraft
  dwells are minutes apart in the median, the deep-sweep block
  excepted by design.
- A universal audio emissions census rides every captured
  window: detection over the window spectrum, per-candidate
  extraction, modulation classification, demodulation, snippet
  filing, and a voice-likelihood grade from hard gates plus a
  rubric the negative controls (static, squelch bursts, music,
  data) cannot pass. Unknown audio is reported as unknown
  audio, with its evidence. Data emissions are filed as data.
- ACARS decodes on the real source in pure Python (MSK
  demodulation, ARINC 618 assembly, parity and CRC gates).
- The amateur desk decodes CW in pure Python and binds FT8 to
  the host's jt9 program when present. The ISS desk flies
  downlink passes: voice filed, SSTV (Robot 36, PD120, Martin
  M1/M2) decoded in pure Python, silent passes reported as
  silent.
- VDL2 binds the host's dumpvdl2 when present and runs a burst
  census that claims no frames when it is not. Digital voice
  is a signalling metadata desk via DSD-FME: intelligible
  voice needs an AMBE-family vocoder this build does not
  carry, and its products say so.
- Transcription is a bounded triage over grade-A census
  snippets on the host's faster-whisper stack: gated, budgeted
  at 90 CPU-minutes a watch, rate-capped, fully accounted.
  Simulated watches never transcribe.
- HFDL remains benched, with its blockers stated in the desk
  inventory: the decoder chain has no user-space staging route
  on the watch host, its channels need a propagation-driven
  band rotation this build does not carry, and the fixed
  antenna is a VHF/UHF antenna.
- The signal-processing desks (census, ACARS, CW, SSTV) use
  numpy, present on the watch host. The rest of the system is
  standard library only; a host without numpy files those
  desks' capture failures as gaps with the import error stated,
  and the watch stands.

License: AGPL-3.0, the Free Computation standard.

Repo: https://github.com/freecomputation/MULTIDEX

