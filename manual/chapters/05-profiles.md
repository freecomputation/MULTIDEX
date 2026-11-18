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

