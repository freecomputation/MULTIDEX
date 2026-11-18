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

