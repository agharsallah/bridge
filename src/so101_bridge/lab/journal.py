"""The lab journal: one line per action that ran (or was refused), newest last.

It answers "what did I do in this experiment?" for the operator, and gives a model or an LLM the recent
history next to the present state (``/<lab>/observe``). Plain JSON lines in var/lab/<lab>.journal.jsonl,
trimmed to the last ``KEEP`` entries when it grows past ``MAX_BYTES``.
"""

import json
import threading
import time
from collections import deque

KEEP, MAX_BYTES = 2000, 1_000_000
_LOCK = threading.Lock()


def add(path, action, args, res):
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
             "args": {k: v for k, v in (args or {}).items() if k != "confirm"}, "ok": bool(res.get("ok"))}
    if res.get("error"): entry["error"] = res["error"]
    elif res.get("message"): entry["message"] = res["message"]
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f: f.write(json.dumps(entry, default=str) + "\n")
        if path.stat().st_size > MAX_BYTES:
            path.write_text("".join(deque(path.open(), maxlen=KEEP)))
    return entry


def tail(path, n=50):
    if not path.is_file(): return []
    with _LOCK, path.open() as f:
        lines = deque(f, maxlen=max(1, min(int(n), KEEP)))
    out = []
    for line in lines:
        try: out.append(json.loads(line))
        except ValueError: pass
    return out
