# 03 Your first watch

This chapter stands a first watch end to end on the shipped defaults with one change that is not optional: the home position. Everything else in the shipped profile is a complete, sane watch as it stands.

## Set the home position

The shipped default profile carries a reference position in the central United States, latitude 39.0, longitude -98.0, altitude 300 m, region "the central United States". Those coordinates are reference values only. Satellite passes, radiosonde windows and position reporting are all computed from this point, so a real watch flown on the reference position reports the wrong sky: it will hunt passes that are not overhead and miss the ones that are.

Copy the shipped profile under a new name in the home's profiles folder and edit the home section of the copy:

```
mkdir -p ~/.multidex/profiles
cp profiles/default.toml ~/.multidex/profiles/home.toml
```

In the copy, set lat and lon to the position of the antenna, alt_m to its altitude in metres, region to the region-level words the products may use for the position, and antenna to the antenna actually connected. The position itself stays local. Products describe it at region level only, and the muster gate checks that a position is set without printing it. Chapter 05 walks every key in the profile; the home section is the only one a first watch must touch.

Confirm the edited profile passes its gates before standing anything:

```
./bin/multidex --profile home doctor
```

## Run plan and read the bill

The plan verb builds the watch bill for the profile starting now and prints it without standing anything. It is the rehearsal of the watch: the same bill builder the officer will use, over the same gates, with no tuner taken.

```
./bin/multidex --profile home plan
```

The first line states the profile name, the start time, the item count and the class split. Each following line gives one bill item: start time, class and kind, label, centre frequency and the desks it carries. Read a first bill with three questions. Are the Class A items the ones the sky over the home position should produce: passes at plausible hours and sonde windows near the profile's synoptic hours? Does the Class B rotation cover the services you care about at a cadence you accept? Do the Class C sweeps reach the bands you expect, including the deep sweep hours? Chapter 06 explains the classes. If the bill is wrong, the watch will be wrong in the same way, and the time to fix it is now, in the profile.

## Stand the watch

```
./bin/multidex --profile home watch
```

The watch verb musters first. If any gate fails, the verb refuses with the failed gates named and their reasons, exits with code 2, and starts nothing. If the muster passes, the officer writes the bill into the new watch folder, records the desk benches for this watch in the open, publishes its first state, and begins executing the bill. The verb itself runs for the length of the watch: the officer is the process you started. It is normally stood in a terminal session or under a service manager that will keep it alive for the full watch length, and it holds an exclusive lock on the home for its lifetime. A second watch started in the same home while the first officer lives is refused by name, with the running watch and its officer process identified.

The watch id is formed from the start time in UTC, for example watch-20261118-000225. Everything the watch produces lives in the folder of that name under the home's watches folder.

## Read status

From any other terminal, the status verb reads the running watch's own published state:

```
./bin/multidex status
```

Status reports the watch id, elapsed and remaining time, what the officer is doing now, counts of events, dossiers and FLASH alerts, the benched desks if any, and the source flown, with the source note printed where the source was substituted. Status never guesses: a watch whose officer process is gone is reported as not running and named as interrupted, and a folder with no state file is never reported as running. Chapter 10 covers the interrupted case and the resume verb that answers it.

## Stop it early, and what a stand-down produces

```
./bin/multidex stop
```

The stop verb writes a stop flag into the running watch's folder. The officer sees it at the next dwell boundary, stands the watch down early, and renders its Watch Report for the elapsed portion, marked as ended early with the reason "operator stop". Stop is therefore not an abort that discards the day: four hours of a stopped watch produce the full product set for four hours, with a coverage account that covers exactly the elapsed span and says why it ends there. If no officer process is alive, stop writes nothing and says so; a dead watch cannot be stood down, only resumed or left as its record stands.

A completed or stood-down watch folder holds the bill, the watch metadata, the state file, the event log, the rendered Watch Report, any SITREPs and FLASH files, the filed snippets, and one stderr log per desk worker. Chapter 08 describes each product. The Index keeps the watch's events and dossiers after the folder's products are rendered, so the watch remains queryable with the ask verb for as long as the home is kept.

