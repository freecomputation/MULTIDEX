# 07 Desk reference

A desk is a decoder worker behind a pipe contract: it receives a normalized feed for its channels inside a dwell window and returns normalized events, and nothing else. It cannot retune the radio and cannot write outside its own output; snippet bytes travel back inside an event and the Watch Officer files them. Tier 1 desks decode identified services. Tier 2 desks work scheduled or windowed services, several dependent on external decoders. Tier 3 desks census populations and spectrum without decoding content.

A desk's state for a watch is computed once, from the build benches, muster benches, supervisor benches, source capabilities and profile roster together, and every surface reads that one computation: doctor, status, the muster events and the Watch Report cannot disagree about which desks are down. This chapter gives every desk, its channels or windows, what it produces and files, and its status in the current release. The benches are stated in their sections and collected at the end.

| Desk | Tier | Channels or window | Status |
| --- | --- | --- | --- |
| adsb | 1 | 1090 MHz | standing |
| airband | 1 | eight channels, 25 kHz each | standing |
| milair | 1 | eight channels, 25 kHz each | standing |
| acars | 1 | 131.45, 131.55, 131.725 MHz | standing |
| ais | 1 | 161.975, 162.025 MHz | standing |
| wxradio | 1 | 162.475 MHz | standing |
| ism | 1 | window (433 and 915 MHz) | standing |
| aprs | 1 | 144.39 MHz | standing |
| pager | 1 | 929.6125 MHz | standing |
| satellite | 2 | 137.1 MHz, 150 kHz | standing, ephemeris permitting |
| sonde | 2 | window (403 MHz) | standing, stated decode limit |
| broadcast | 2 | window (FM band) | standing |
| vdl2 | 2 | 136.975 MHz | standing where dumpvdl2 is present |
| amateur | 2 | window | standing; FT8 lines need jt9 |
| digitalvoice | 2 | window | standing where dsd-fme is present |
| hfdl | 2 | window | standing where dumphfdl is present |
| iss | 2 | 145.8 MHz | standing |
| cellular | 3 | window | standing, metadata only |
| trunking | 3 | window | standing, metadata only |
| audiocensus | 3 | window | standing |
| sweep | 3 | window | standing |

## adsb

Tier 1, light CPU, service ADS-B aircraft. The desk decodes Mode S frames in process on 1090 MHz, discarding frames that fail their CRC. Identification frames (typecodes 1 to 4) file decode events with the ICAO address and callsign, CONFIRMED. Position frames (typecodes 9 to 18) file telemetry with position and altitude: a position stitched from an even and odd frame pair within the 10-second CPR pairing window is CONFIRMED as a global decode; one decoded against the home position as local reference is PROBABLE, with the method named. Velocity frames (typecode 19) file telemetry with the velocity record. No snippets are filed.

## airband

Tier 1, moderate CPU, service airband voice. The desk works eight channels of 25 kHz each: 118.075, 121.100, 121.500, 125.400, 126.200, 133.450, 133.750 and 135.200 MHz, inside the airband rotation windows, whose centres cycle through 121.3, 118.6, 125.8 and 134.3 MHz. It files a voice event per detected segment with channel, signal to noise ratio and duration. A segment with a heard callsign files PROBABLE with the callsign as emitter; without one, UNRESOLVED. Guard frequency activity (121.5 MHz by guard_hz) files as an alert and is a standing FLASH trigger. Segments at 18 dB or better, and all guard segments, file their audio as a voice snippet under the caps; on the real source the snippet is the captured audio itself. The desk reports activity, not transcripts.

## milair

Tier 1, moderate CPU, service military air voice. The desk works eight channels of 25 kHz each: 239.275, 243.000, 248.550, 270.350, 319.250, 327.125, 336.300 and 371.350 MHz, and the milair rotation entry cycles its centre through the same eight channels. It files voice events on the airband desk's terms: a segment with a heard callsign files PROBABLE with the callsign as emitter, without one UNRESOLVED, and segments at 18 dB or better file their audio as a voice snippet, the captured audio itself on the real source. The UHF guard channel at 243.000 MHz is one of its channels and is worked as one: this desk gives it no special alert treatment, and its segments file as voice like any other channel's.

## acars

Tier 1, light CPU, service ACARS datalink. The desk decodes ACARS messages on 131.45, 131.55 and 131.725 MHz, in the package's own message layer, on both sources. Each decoded message files a decode event with the tail as emitter, the flight, the label and the message text, CONFIRMED, and the raw frame rides in the event data. No host decoder program is involved in this desk.

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

Tier 2, heavy CPU, service weather satellite passes. The desk flies the Class A passes on a 150 kHz channel at the satellite frequency. Its events carry the prediction graded PROBABLE, with the pass geometry, beside the reception result. A NOAA pass with lock files a pass event at CONFIRMED with the measured signal quality. The evidence is the captured pass audio, filed whole as a satellite snippet, and the audio is decoded by the package's own APT decoder: when the recording carries decodable APT, the decoded image is filed with the same event and named in its data, and when it does not, the event states the decoder's reason and files the audio alone. The desk never invents an image. A downlink that is not classified for imagery in this build is stated as such on its pass event, with the audio filed. METEOR passes are the standing exception to all of this, under the acknowledgement policy: the desk files one pass event stating that the pass was acknowledged and not recorded, no capture is attempted on either source, no audio or IQ is filed, and the pass carries a zero-byte allocation with no reservation and no grant. A predicted pass with no lock files a pass event at UNRESOLVED with the failure recorded as "no lock": a miss is a record, and the SITREP's passes taken and missed come from these events. The desk benches at muster when no orbital elements are loaded or the elements are over 30 days old.

## sonde

Tier 2, light CPU, window-fed, service radiosondes. The desk works the Class A sonde windows at 403 MHz. On the simulated source it decodes telemetry frames, filing telemetry per frame with the serial as emitter, position and altitude, and a pass event when a flight completes at burst, a standing FLASH trigger. On the real source it censuses the window: a carrier present in the band files a census event at PROBABLE whose content states that telemetry is not decoded, because an RS41-class decoder is not bound in this build, with the carrier peak and noise floor in the data. The desk never invents a track; the limit is printed on the event itself.

## broadcast

Tier 2, light CPU, window-fed, service broadcast census. The desk censuses the FM broadcast band inside the fmband window. Each station found files a census event with frequency and peak power: CONFIRMED where an RDS identity was read, with the RDS name as emitter, and PROBABLE for a carrier without a read identity. Its product is a population record of the local broadcast band, against which change across watches is visible: a station that leaves the air or a new one that arrives changes the census, and the dossier layer holds the history.

## vdl2

Tier 2, light CPU, service VDL2 datalink, channel 136.975 MHz. The desk stands where the host carries the dumpvdl2 program, and its events say so: each decoded message files a decode event at CONFIRMED whose content names the bound program, with the aircraft tail as emitter where the frame carries one, the message text and the raw frame in the data. Burst energy on the channel with no frame decoded files an observation at UNRESOLVED, a burst census and nothing more. Where dumpvdl2 is absent, the desk benches at muster with the reason stated, a staging instruction naming the program to install, and no product claims VDL2 traffic from such a watch.

## amateur

Tier 2, moderate CPU, window-fed, service amateur CW and digital. The desk stands on its windows on both sources. CW is decoded in process by the package's own decoder: each decoded signal files a decode event at PROBABLE whose content states the speed and the decoder's element confidence and marks unresolved characters with a question mark, and a callsign following the DE pattern becomes the emitter. FT8 is decoded by the host's jt9 program where it is present: each decoded line files a decode event at CONFIRMED whose content names the bound program. Where jt9 is absent the desk still stands for CW, and doctor states the FT8 bench on its own line. SSB voice on the desk's windows is not this desk's catch; it belongs to the audio census desk.

## digitalvoice

Tier 2, heavy CPU, window-fed, service DMR, P25 and NXDN digital voice. The desk stands where the host carries the dsd-fme program. It reports the signalling that program decodes: each call files a decode event at CONFIRMED with the system, talkgroup, source and slot in its data and its encryption state stated, in the clear or encrypted. The desk does not decode voice, and no event claims otherwise: intelligible digital voice needs an AMBE-family vocoder this build does not carry.

What the desk archives instead is the vocoder evidence itself. The bound program writes a frame recording beside its log for each call it can hear in the clear, and the desk harvests the de-interleaved vocoder parameter frames from those recordings byte-exact and files them as payload snippets on the call's event, linked to the emitter's dossier, at about 306 bytes per second of speech. The archive is stated for what it is on every event that carries it. AMBE+2 frames are archived undecodable until the in-house synthesizer exists and the US8359197 gate opens on 2028-05-20. IMBE frames await the in-house IMBE build. Payload evidence is permanent: it is exempt from shedding, and it is filed even when nothing sheddable remains in the pool. An encrypted call archives nothing, because the bound program writes no frames for a call it cannot hear. Where dsd-fme is absent, the desk benches at muster with the reason stated.

## hfdl

Tier 2, light CPU, window-fed, service HF datalink. The desk stands where the host carries the dumphfdl program, on the HF rotation windows. Each decoded frame files a decode event at CONFIRMED whose content names the frame kind and the parties, source and destination, decoded by the bound program, with the aircraft address as emitter where the frame carries one and the raw frame in the data. Squitters and logon traffic are reported as the network signalling they are. Where dumphfdl is absent, the desk benches at muster with the reason stated, and HF spectrum continues to be censused by the sweep desk on the HF windows.

## iss

Tier 2, light CPU, service ISS voice and SSTV, channel 145.8 MHz. The desk stands, and ISS passes fly with it. Voice activity on the downlink files a voice event at PROBABLE per segment, with the audio filed as a snippet; this desk does not transcribe it. SSTV is decoded in process by the package's own SSTV decoder: a decoded image files a decode event at CONFIRMED stating the mode and how many of its lines were recovered, and the image is filed as received, unretouched. A pass on which the station was silent, the common case, files an observation at UNRESOLVED stating that no voice and no SSTV were decoded this pass.

## cellular

Tier 3, light CPU, window-fed, service cellular survey. The desk surveys the cellular window at 752.5 MHz and files census events per cell: band, cell identity as emitter, and reference signal received power, each marked metadata only in its data and content. It does not decode cellular content, and under the eighth law it never will: cellular traffic is encrypted traffic, and encrypted traffic is metadata by construction.

## trunking

Tier 3, light CPU, window-fed, service trunking census. The desk censuses the trunking window at 851.75 MHz and files census events per system, marked metadata only. On the simulated source a system files with its system id, site count and active talkgroup count, CONFIRMED. On the real source the desk works from the scan census: a persistent carrier files a census event at PROBABLE whose content states that it is consistent with a control channel, metadata only, and that system identity and talkgroup counts are not decoded by this build. The desk never files voice and never claims a talkgroup it did not decode.

## audiocensus

Tier 3, moderate CPU, window-fed, service universal audio census. The desk works every window its dwell carries, demodulating what the window holds and grading each audio-band emission by voice likelihood. An extracted emission files an audio event under the emitter AUDIO: followed by its frequency: grade A and B candidates file at PROBABLE, grade C at UNRESOLVED, each stating the modulation estimate, the voice likelihood grade and score as a machine estimate, the signal to noise ratio and the sampled duration, with the audio itself filed as a snippet. Grade A events are also marked as transcription candidates, the only road into the transcription queue of chapter 05. An emission the census identifies as data, constant-envelope with discrete tones, files an observation at PROBABLE stating it is not voice and was not graded as voice. Grade D energy files an observation at UNRESOLVED, consistent with noise or a burst, and no snippet is filed. Extraction is capped per window: emissions past the cap are census-counted only, and a further event states how many were deferred, so the cap is a record and not a silence. Narrow carriers under 400 Hz wide are logged as a count in one observation, with no audio extracted. The desk's honesty rule is printed on its own design: unknown audio is reported as unknown audio, and no event from this desk claims decoded speech.

## sweep

Tier 3, light CPU, window-fed, service spectrum census and anomaly. The sweep desk is the census engine of the watch, speaking for every window no other desk claims. It turns a window's spectrum into observations: one event per emission, with frequency, shape classification, bandwidth, peak power, the measured noise floor and the band name from the band plan. A shape that starts from "unknown" files UNRESOLVED; a classified shape files PROBABLE. A peak 30 dB or more over the floor marks the event an interrupt candidate, the input to the officer's interrupt machinery in chapter 06. The desk feeds the fmband, HF and Class C sweep items and takes the interrupt dwells. Its observations accumulate into emission dossiers, so an unidentified shape returning nightly at one frequency becomes a known unknown with a history, the honest end-state for a signal nobody has decoded yet.

## The bench list, collected

Stated plainly, as the current release's limits: no desk is benched by the build itself, on either source. On the real source, three desks stand only where their bound program is present on the host: vdl2 where dumpvdl2 is present, digitalvoice where dsd-fme is present, and hfdl where dumphfdl is present. Where the program is absent the desk benches at muster with the staging instruction as its reason, and doctor shows the same bench before any watch is stood. Two further states are host-conditional in the same way: transcription binds where the host's faster-whisper stack is importable and benches with its reason where it is not, and the amateur desk's FT8 lines bench where jt9 is absent while the desk itself stands for CW. The satellite desk benches at muster when no orbital elements are loaded or the elements are over 30 days old. Two partial limits ride inside standing desks, stated on their own events: the sonde desk on the real source censuses carriers without decoding telemetry, and the satellite desk decodes imagery only for the NOAA APT downlink. Every bound program named in this chapter is a dependence under the independence program of chapter 13, stated in doctor's dependence census and retired in turn by an implementation of the system's own. The APT decoder was the first retirement: no host program decodes APT for this build.

