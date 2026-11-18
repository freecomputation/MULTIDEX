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

