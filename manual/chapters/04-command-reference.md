# 04 Command reference

The command surface is seven verbs and nothing else. No verb asks a question back. Anything a verb needs comes from the profile, the defaults or its arguments, and anything it cannot get is a refusal with the reason. Global options come before the verb: --home names the data home, resolved as --home first, then the MULTIDEX_HOME environment variable, then ~/.multidex; --profile names the profile, resolved as a path when the name is an existing file, then as a name in the home's profiles folder, then in the repository tree's profiles folder. The special profile name "default" with no file present loads the built-in defaults, which carry the same values as the shipped default.toml.

| Verb | Purpose |
| --- | --- |
| watch | Stand the watch (with --resume, resume an interrupted watch) |
| plan | Build and print the watch bill without standing anything |
| status | State of the running watch |
| sitrep | Render a SITREP for the current watch now |
| stop | Stand the running watch down at the next dwell boundary |
| ask | Query the Index |
| doctor | Muster checks and desk inventory without standing a watch |

## watch

Usage: `multidex [--home DIR] [--profile NAME] watch [--resume WATCH_ID]`

The watch verb loads the profile, resolves the source and runs the muster gates. On a gate failure it prints the refusal with each failed gate named, exits with code 2 and starts nothing. On a pass it creates the watch folder, writes the bill and the watch metadata, files the muster event and one desk health event per benched desk, publishes the first state, and executes the bill dwell by dwell for the profile's watch length. The process is the officer: it runs in the foreground for the whole watch and holds the home's watch lock throughout.

A start that finds another watch's officer alive in the same home is refused, with the running watch and its officer process named in the refusal. One home stands one watch at a time, and one tuner serves one watch.

With --resume, the verb takes the id of an interrupted watch. Resume re-runs the muster gates against the current footing, re-files the bench set, and continues the original bill from the first dwell that never filed its dwell record; recorded dwells are never re-flown, and FLASH numbering continues from the files on disk. A watch whose officer is still alive is not interrupted and cannot be resumed, and neither can a completed watch; the refusal says so. Chapter 06 describes the mechanics in full.

On normal completion or stand-down, the verb prints the watch id and the path of the watch folder holding the products, and exits with code 0.

## plan

Usage: `multidex [--home DIR] [--profile NAME] plan`

Plan loads the orbital elements, builds the bill for a watch starting now, and prints it. The header line gives the profile name, the start time, the total item count and the counts by class. Each item line gives the start time, class and kind, label, centre frequency and the desks carried, or "no desks" for an item flown by the census alone. Plan takes no tuner, writes nothing to the Index, and exits with code 0. It is the bill the watch verb would execute if stood at that moment, subject to the muster the watch verb additionally runs.

## status

Usage: `multidex [--home DIR] status`

Status reads the home's current watch pointer, validates it against the disk, and prints the running watch's published state: watch id, elapsed and remaining time, current activity, counts of events, dossiers and FLASH alerts, the benched desks or the statement that all standing desks are healthy, and the source kind. A watch on a substituted source carries its source note. When no watch is running, status says so in terms that match the disk: the last completed watch is named when the pointer names one, an interrupted watch is named as interrupted with the resume invocation given, and a folder with no state file is reported as a watch no officer is reporting for. Status exits with code 0 in all these cases; it is a report, not a gate.

## sitrep

Usage: `multidex [--home DIR] sitrep`

The sitrep verb renders a SITREP for the current watch now, outside the automatic cadence, and writes it into the watch folder with the next sequence number. It resolves the current watch allowing a completed one, so a SITREP can also be rendered over a finished watch's record. When no watch exists yet, it prints that there is nothing to report and exits with code 1. Chapter 08 describes the SITREP's contents.

## stop

Usage: `multidex [--home DIR] stop`

Stop determines which watch is actually running by liveness, not by the pointer alone: a watch is running only when the process id its state file recorded is still alive. For a live watch, stop writes the stop flag into that watch's folder and prints that the watch stands down at the next dwell boundary and renders its Watch Report for the elapsed portion. The verb then exits with code 0; the stand-down itself is the officer's work, performed when the flag is seen. When the pointer claims a watch whose officer is gone, stop prints that the watch stands interrupted and there is no officer process to stand down, writes no flag, and exits with code 1. When nothing is running at all, it prints that no watch is running and exits with code 1.

## ask

Usage: `multidex [--home DIR] ask QUERY [WORDS ...]`

Ask takes a query of one or more words, joined into a single query string, and answers from the Index. The query forms are an emitter key, a frequency, a band query and a desk name, with a dossier substring search as the fall-through. The output forms are described with worked examples in chapter 08. Ask reads only the Index in the home; it opens no tuner and writes nothing. It exits with code 0 whether or not the Index answers, and an unanswered query prints the forms it does answer to, so the operator can re-ask in a form the Index understands.

## doctor

Usage: `multidex [--home DIR] [--profile NAME] doctor`

Doctor runs the muster gates for the loaded profile without standing a watch, prints each gate's verdict and evidence, then prints the desk inventory: every desk with its tier, service, state for this profile and source, and the reason for any bench, followed by the transcription state line and the source capability line. Doctor exits with code 0 when no gate reports FAIL and code 1 when any does. A profile that fails to load is a refusal printed as such, with exit code 2, and this applies to every verb that loads a profile.

