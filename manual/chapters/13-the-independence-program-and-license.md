# 13 The independence program and license

Earlier editions of this chapter described a program of work as forthcoming. Every item in that program has shipped, and the first section below records it as delivered, briefly, because the record belongs in the manual. The chapter's standing subject is the program that replaced it: the independence program, under which every external program the system still binds is a stated dependence, retired in turn by an implementation of the system's own.

## The standing-up program, delivered

The 0.2.0 release stood the benched desks up. ACARS decodes on the real source through the package's own physical layer and frame assembly. CW and SSTV decode through the package's own decoders. The universal audio emissions census stands as a desk in its own right, chapter 07's audiocensus, grading every audio-bearing emission its windows hold and reporting unknown audio as unknown audio. VDL2, digital voice and HFDL stand through bound host programs, and FT8 through the host's jt9. The same release repaired the airband geometry the old chapter diagnosed: the channel set was replaced with the eight-channel set chapter 07 lists, the rotation centres cycle so every configured channel falls inside a captured window, the airband and ADS-B rotation weights were raised, and the UHF military airband desk was added as a voice desk in its own right, on the terms its section in chapter 07 states.

Later releases continued the same direction. The 0.4.2 release replaced per-watch reception budgeting with the standing allocation ledger of chapter 08. The 0.5.0 release retired the APT binding: satellite imagery is decoded by the package's own APT decoder, and no host program decodes APT for this build. The 0.6.0 release added the digital voice desk's vocoder frame archive, chapter 07, and set the METEOR policy to acknowledgement only, removing the SatDump binding and the LRPT module entirely rather than replacing them. The 0.7.0 release added the live update system of chapter 14. One item from the old program remains open and is stated as open: radiosonde telemetry decode on the real source, where the sonde desk still censuses carriers and says so on its events.

## The independence program

The law of the program is plain: the system ends with zero dependence on any other person's program. Every external program the build invokes is a stated debt until an implementation of the system's own retires it. The operating system and its system libraries are substrate, not dependence; numpy stands in that class, a library the signal-processing modules lean on. Nothing else is exempt by courtesy.

The debts are not a matter of recollection. Doctor prints the dependence census from the binding table itself: each program named with the work it carries and whether it is present on the host. As this manual is written the census holds the tuner programs rtl_sdr and rtl_fm, rtl_433 for the ISM desk, multimon-ng for the audio-borne formats, jt9 for FT8, dumpvdl2, dsd-fme and dumphfdl for their desks, the faster-whisper stack for transcription, chronyc for the clock gate, git for the live update system, and the audio players that sound the FLASH chime. A binding that is absent benches its desk with the staging instruction as the reason, so a dependence is visible in the products even on a host that does not carry it.

Retirement has an order, and the order is worked in sequence: the APT decoder first, retired in 0.5.0; then the pager protocols, the ISM protocols, an own tuner capture path, the ACARS message layer, VDL2, HFDL, digital voice, and the FT8 family; LRPT stands retired by removal under the METEOR policy rather than by replacement; and transcription last, the hardest case, flagged as such. A retirement lands only when the own implementation passes the same fixtures and real captures the binding passed. Until it lands, the doctor output states the binding as a dependence, in those words, and no product describes a bound desk as if the system carried it alone.

## License

MULTIDEX is free software, released under the GNU Affero General Public License, version 3. The full license text ships with the repository as its LICENSE file and governs every copy, modification and network use of the system. This manual is part of the same work and is published under the same terms.

Free Computation

