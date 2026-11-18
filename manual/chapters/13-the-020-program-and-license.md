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

Free Computation

