# 02 Getting started

## Hardware

The receiver is an RTL-SDR Blog V4 class USB dongle: a software defined radio that hands the host raw I/Q samples across a tuning range that covers HF through L-band, with a usable instantaneous bandwidth of about 2.4 MHz per tuning. That one window is the physical fact the whole design is built around. Chapter 01 states the law it produces: the watch is the art of spending one window well.

The antenna matters more than any software in this manual. The profile records which antenna is on the tuner at muster, because the coverage account is meaningless without it: a watch that heard nothing on a given band means one thing with a general coverage antenna and another with a discone or a long wire. The shipped default profile names a general coverage antenna. Set the profile's antenna line to what is actually connected before a real watch, so the record says what stood the watch.

The host is any machine that can stay up for the full watch length, hold the disk ceiling the profile sets, and run the host tools listed below. Sleep states and power saving that suspend the machine or the USB bus will tear gaps in the record, and the record will say so. The host clock must be true: passes, sonde windows and every timestamp are computed against it, and the muster clock gate fails a watch whose clock is more than one second off network time where the offset can be measured.

## Software requirements

MULTIDEX itself is Python, standard library only, and requires Python 3.11 or later. It is run from its repository tree; there is nothing to compile and no package index is consulted at run time.

The real source binds a small set of host tools, and the doctor verb checks for the footing they provide. The tools are the rtl-sdr suite, which supplies rtl_sdr for I/Q captures and rtl_fm for audio demodulation; rtl_433, which decodes the ISM device population; and multimon-ng, which decodes the audio-borne formats the build delegates to it, among them POCSAG paging, APRS packet audio and weather radio SAME headers. A machine without these tools can still run MULTIDEX on the simulated source, and chapter 09 describes exactly what that means and how the substitution is stated when it happens unasked.

## Installation

Clone the repository and run the launcher from the tree:

```
git clone https://github.com/freecomputation/MULTIDEX
cd MULTIDEX
./bin/multidex doctor
```

The launcher at bin/multidex runs the package in place. The equivalent module form is:

```
python3 -m multidex doctor
```

Global options come before the verb on every invocation. The two global options are --home, which names the data home, and --profile, which names the profile. The home is where everything the system writes lives: the Index, the watch folders, the profiles the operator keeps, and the current watch pointer. Its resolution order is fixed. The --home option wins when given. Otherwise the MULTIDEX_HOME environment variable is read. Otherwise the home is the .multidex folder in the operator's home directory. A typical explicit invocation looks like this:

```
./bin/multidex --home ~/multidex-home --profile default doctor
```

## Running doctor and reading its output

Doctor is the muster without the watch. It runs the same gates a watch must pass, then prints the full desk inventory. Run it after installation, after moving the dongle, after editing a profile, and whenever a watch refuses to start. Its exit code is 0 when no gate reports FAIL and 1 when any gate does.

The checks section prints one line per gate, each marked PASS or FAIL with the evidence in plain words. The gates are these:

| Gate | What it verifies |
| --- | --- |
| tuner | The source can be stood: the dongle and its tools on the real source, or the simulated source's readiness |
| clock | The host clock is plausible and, where chrony is present, within one second of network time |
| disk | Free space on the home volume meets the profile's disk ceiling |
| profile | The loaded profile validates with no problems |
| home position | The home position is set and is not the unset point at 0, 0 |
| ephemeris | Orbital elements are loaded and their age is stated; elements over 7 days old degrade pass scheduling to PROBABLE, and over 30 days old bench the satellite desk for the watch |

The ephemeris gate never fails a watch. It reports, and the benching it describes happens in the open: a watch stood on stale elements says in its own record that the satellite desk is benched and why.

Below the checks, doctor prints the desk inventory: every desk in the system with its tier, its service name, its state for this profile and source, and the reason for any bench. The last lines state the transcription state and the source capabilities. Chapter 07 is the full desk reference; the doctor inventory is the same truth computed for the machine in front of you, on the day you ask. When the two ever disagree, the doctor output is the one that describes your installation, and the disagreement itself is worth investigating before a watch is stood.

One doctor behavior deserves plain statement. If the profile asks for the real source and the real source cannot be stood, doctor does not present a bare tuner failure. It shows the footing the watch would actually fly on: the simulated source, with the substitution stated in the tuner line and the capability line. A watch stood in that condition carries the same statement in its muster output, its status and every product. The substitution is never silent. Chapter 09 covers the simulated source in full.

