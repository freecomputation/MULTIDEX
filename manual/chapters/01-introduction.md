# MULTIDEX

An autonomous 24-hour signals watch on a single RTL-SDR

Free Computation

Manual for MULTIDEX 0.1.3

# 01 Introduction

MULTIDEX is an autonomous signals watch system built on a single RTL-SDR receiver. The operator issues one small command and the system does the rest. It plans a 24-hour watch, schedules the one tuner across everything that tuner can reach, collects, decodes, classifies, correlates and disseminates finished products, all without further human direction. The operator may shape the watch in advance through a profile. Configuration is an option, never a requirement: the shipped defaults are complete and sufficient for a full watch on the first day the system is run.

The name expands as Multi-band Dissemination and Exploitation Index, and the expansion describes the work. The watch is multi-band because one tuner is spent across the whole spectrum it can reach, from HF to L-band. It disseminates because its output is finished products on a schedule, not a raw stream. It exploits because every capture is correlated against everything heard before it, so emitters accumulate dossiers and the unusual is assessed against the ordinary. And it is an index because the record it builds, the Index, is the durable product: a queryable account of what the spectrum around the home position contained, with the evidence filed beside each claim.

## The one-command premise

The premise is that a watch is a standing order, not a session. A conventional receiver setup asks the operator to be present: to choose frequencies, to notice activity, to decide what to record and what it meant. MULTIDEX moves all of that into the system. The operator states intent once, in a profile or in the defaults, and the watch runs for its full length on its own authority. If a question would need the operator mid-watch, the system answers it in advance from the profile, answers it from the defaults, or defers it to the products. No verb asks a question back, and no desk pages the operator at 03:00 to ask what a signal is. The signal is recorded, graded, filed, and reported in the next scheduled product.

## The design laws

Eight laws govern the system, and where a feature and a law conflict, the law wins and the feature is cut.

The first law is autonomy. A started watch runs to completion with no human input.

The second law is honesty. The system never invents content. A decode is reported at the confidence the evidence supports, an unidentified emission stays unidentified, and static is never dressed up as a voice. Every event in the Index carries one of three grades: CONFIRMED, where the evidence carries the claim outright; PROBABLE, a strong match with a named gap; and UNRESOLVED, recorded and honestly open.

The third law is coverage. Every product states what was not heard and why. One tuner cannot be everywhere, so every report carries its coverage account: where the tuner was, where it was not, and what classes of event the watch therefore cannot speak to. A product without a gap statement is a defect.

The fourth law is one tuner. The design never pretends the kit is more than it is. One RTL-SDR hears one window of spectrum at a time, and the whole art of the system is how it spends that window across 24 hours. Capabilities that need two tuners at once are out of scope, not faked.

The fifth law is sanity at the gate. A watch starts only when the system has verified its own footing: tuner present and free, clock true, disk headroom real, profile valid. If the footing fails, the system refuses with the reason in plain words and starts nothing.

The sixth law is locality. Everything stays on the operator's machines. There is no network dependence, no account, no telemetry, and no product leaves the host except by the operator's own hand.

The seventh law is sovereignty. The operator's stop is immediate and final, and the tuner is the operator's. If the operator takes the tuner, MULTIDEX stands down and records the gap. It never fights for the hardware.

The eighth law is receive only. There is no transmission path in the design, in any desk, at any tier. Encrypted traffic is metadata: presence, timing, volume and endpoints where broadcast. Its content is out of scope by construction, not by policy toggle.

## What MULTIDEX is not

MULTIDEX is not a live listening tool. It makes no sound during a watch, with one exception: a two-tone chime sounds on the host when a FLASH alert is written, if the profile's alerts section enables it, so an alert is audible in the room as well as filed. Tuning around by ear and listening to a conversation as it happens are a different program's work. MULTIDEX files audio evidence as snippets under bounded quotas and reports what it heard in its products; it does not present a speaker feed.

It is not a scanner toy. A scanner dwells where it is told and forgets everything by morning. MULTIDEX plans its day in advance as a written bill, accounts for every dwell and every gap, correlates each emitter into a dossier that persists across watches, and renders a flagship report whose coverage account can be audited against the dwell records in the Index.

It is also not a promise machine. Where a decode needs a program this build does not carry, the desk benches itself and states the reason in the doctor output, in the watch's own record and in the products. Chapter 07 states the current release's desk inventory exactly, including its benches, and chapter 13 describes the program that stands those desks up. Nothing in this manual describes a benched desk as if it were standing.

