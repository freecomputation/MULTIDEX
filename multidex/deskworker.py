"""Desk worker process: the pipe contract from design section 5.

Protocol: one JSON job per line on stdin -
{"job_id": N, "feed": {...}, "ctx": {...}} - and on stdout, one JSON
event per line followed by {"__end__": N}. A desk that raises exits
nonzero: a crash the supervisor can see, never a silent hang. Worker
processes persist for the life of a watch, so a desk may keep state
(the ADS-B desk's CPR pairing does).
"""

import importlib.util
import json
import os
import sys
import traceback


def load_process(name):
    extra = os.environ.get("MULTIDEX_DESK_DIR")
    if extra:
        path = os.path.join(extra, name + ".py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(
                "extdesk_" + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.process
    from .desks import PROCESS
    return PROCESS[name]


def main(argv):
    name = argv[1]
    try:
        process = load_process(name)
    except Exception:
        traceback.print_exc()
        return 3
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        job = json.loads(line)
        try:
            events = process(job["feed"], job["ctx"])
        except Exception:
            traceback.print_exc()
            return 3
        for ev in events:
            sys.stdout.write(json.dumps(ev) + "\n")
        sys.stdout.write(json.dumps({"__end__": job["job_id"]}) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
