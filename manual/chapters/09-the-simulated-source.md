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

